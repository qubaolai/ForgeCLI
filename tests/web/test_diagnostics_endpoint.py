"""Web 侧的诊断接口 (ADR-0035 决策 6).

与 CLI 的 /diagnostics 读同一个函数. 单列一组用例是因为它有一个 CLI 没有的分支:
没有激活项目时进程里根本没有 LLM 网关, 接口不能因此 500.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from forgecli.application.llm.gateway.observability import (
    GatewayCallSample,
    InProcessGatewayMetrics,
)
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.response import FinishReason
from forgecli.interfaces.web.app import create_app


class _Registry:
    def __init__(self, active: object) -> None:
        self.projects = SimpleNamespace(list_trusted=tuple)
        self.active = active

    def close(self) -> None:
        return None


def _client(active: object, static: Path) -> TestClient:
    app = create_app(
        registry=_Registry(active),  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=static,
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    client.get("/boot?token=known-token", follow_redirects=False)
    return client


def test_reports_logging_and_stage_readings(tmp_path: Path) -> None:
    body = _client(None, tmp_path).get("/api/v1/diagnostics").json()

    assert "logging" in body
    assert "stages" in body
    assert body["process"]["pid"] > 0


def test_no_active_project_has_no_gateway_section(tmp_path: Path) -> None:
    """网关是按项目装配的: 没激活项目时它不存在, 接口如实少一段, 不是报错."""
    body = _client(None, tmp_path).get("/api/v1/diagnostics").json()

    assert "gateway" not in body


def test_active_project_reports_model_calls(tmp_path: Path) -> None:
    metrics = InProcessGatewayMetrics()
    metrics.on_call(
        GatewayCallSample(
            provider="openai",
            model="gpt-4o",
            origin=RequestOrigin.ACT,
            finish_reason=FinishReason.STOP,
            latency_ms=120.0,
        )
    )
    runtime = SimpleNamespace(llm=SimpleNamespace(gateway_metrics=metrics))

    body = _client(runtime, tmp_path).get("/api/v1/diagnostics").json()

    assert body["gateway"]["openai/gpt-4o"]["calls"] == 1


def test_diagnostics_needs_the_session_cookie(tmp_path: Path) -> None:
    """它会吐出日志路径与调用读数, 不能是个匿名可读的端点."""
    app = create_app(
        registry=_Registry(None),  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=tmp_path,
    )
    anonymous = TestClient(app, base_url="http://127.0.0.1")

    assert anonymous.get("/api/v1/diagnostics").status_code == 401
