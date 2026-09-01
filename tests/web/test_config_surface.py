"""Web 控制面必须覆盖 CLI 的全部配置能力 (ADR-0025 决策 2)。

这组用例是**清单**: 每条 CLI 斜杠命令能改的东西, Web 都要有对应入口。少一条就说明
Web 用户被迫回终端, 而终端入口已经由 ADR-0025 取代。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from forgecli.application.llm.config.llm_config import (
    ModelParams,
    ModelSpec,
    ProviderConfig,
)
from forgecli.domain.planning import PlanIndex
from forgecli.interfaces.web.app import create_app
from forgecli.shared.serialization import to_jsonable


class FakeRegistry:
    def __init__(self, active: object) -> None:
        self.projects = SimpleNamespace(list_trusted=tuple)
        self.active = active

    def close(self) -> None:
        return None


class FakeRuntime:
    """只记录被调用了什么: 这组用例断言的是接线, 不是 PlanningService 的行为。"""

    def __init__(self) -> None:
        self.busy = False
        self.project = SimpleNamespace(project_id="demo")
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.overrides = {"act": "openai:gpt-x"}

    # /model
    def current_model(self) -> str:
        return "openai:gpt-x"

    def set_current_model(self, provider: str, model: str) -> None:
        self.calls.append(("set_current_model", (provider, model)))

    # /config 用途覆盖
    def model_overrides(self) -> dict[str, str]:
        return dict(self.overrides)

    def set_model_override(self, origin: str, provider: str, model: str) -> None:
        self.calls.append(("set_model_override", (origin, provider, model)))

    def clear_model_override(self, origin: str) -> None:
        self.calls.append(("clear_model_override", (origin,)))

    # /thinking
    def thinking_view(self) -> dict[str, object]:
        return {"model": "openai:gpt-x", "configured": True, "mode": "on"}

    def update_thinking(self, mode: str, effort: str) -> bool:
        self.calls.append(("update_thinking", (mode, effort)))
        return True

    # /status 与 /recovery
    def status(self) -> dict[str, object]:
        return {"session_id": "s1", "mode": "plan", "model": "openai:gpt-x"}

    def recovery_status(self) -> dict[str, object]:
        return {"checkpoint_count": 2, "pending": []}

    # /plan-doc
    def plan_index(self) -> PlanIndex:
        return PlanIndex(active_plan_id="p1")

    def activate_plan(self, plan_id: str) -> bool:
        self.calls.append(("activate_plan", (plan_id,)))
        return plan_id == "p1"


def _client(runtime: FakeRuntime, tmp_path) -> TestClient:
    app = create_app(
        registry=FakeRegistry(runtime),  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=tmp_path,
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    client.get("/boot?token=known-token", follow_redirects=False)
    return client


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.get("/api/v1/bootstrap").json()["csrf_token"]}


def test_current_model_can_be_chosen_from_the_web(tmp_path) -> None:
    """CLI 的 /model: 选运行时默认模型。原来 Web 只能手写两个配置键。"""
    runtime = FakeRuntime()
    with _client(runtime, tmp_path) as client:
        response = client.put(
            "/api/v1/models/current",
            json={"provider_id": "openai", "model_id": "gpt-x"},
            headers=_csrf(client),
        )

    assert response.status_code == 200
    assert ("set_current_model", ("openai", "gpt-x")) in runtime.calls


def test_per_origin_overrides_can_be_set_and_cleared(tmp_path) -> None:
    """CLI 的 /config 用途模型覆盖: 原来 Web 完全没有入口。"""
    runtime = FakeRuntime()
    with _client(runtime, tmp_path) as client:
        headers = _csrf(client)
        client.put(
            "/api/v1/model-overrides/classifier",
            json={"provider_id": "openai", "model_id": "gpt-mini"},
            headers=headers,
        )
        client.delete("/api/v1/model-overrides/classifier", headers=headers)

    assert ("set_model_override", ("classifier", "openai", "gpt-mini")) in runtime.calls
    assert ("clear_model_override", ("classifier",)) in runtime.calls


def test_thinking_is_adjustable_at_runtime(tmp_path) -> None:
    """CLI 的 /thinking: 进程内覆盖, 与模型配置里的持久 thinking 是两回事。"""
    runtime = FakeRuntime()
    with _client(runtime, tmp_path) as client:
        response = client.post(
            "/api/v1/thinking",
            json={"mode": "on", "effort": "high"},
            headers=_csrf(client),
        )

    assert response.json()["changed"] is True
    assert ("update_thinking", ("on", "high")) in runtime.calls


def test_status_and_recovery_are_readable(tmp_path) -> None:
    """CLI 的 /status 与 /recovery。"""
    with _client(FakeRuntime(), tmp_path) as client:
        status = client.get("/api/v1/status").json()
        recovery = client.get("/api/v1/recovery").json()

    assert status["session_id"] == "s1"
    assert recovery["checkpoint_count"] == 2


def test_plans_can_be_listed_and_switched(tmp_path) -> None:
    """CLI 的 /plan-doc list | use <id>。"""
    runtime = FakeRuntime()
    with _client(runtime, tmp_path) as client:
        headers = _csrf(client)
        listed = client.get("/api/v1/plans").json()
        activated = client.post("/api/v1/plans/p1/activate", headers=headers)
        missing = client.post("/api/v1/plans/nope/activate", headers=headers)

    assert listed["active_plan_id"] == "p1"
    assert activated.status_code == 200
    assert missing.status_code == 404


def test_config_changes_are_refused_while_a_turn_runs(tmp_path) -> None:
    """与既有模型 / 授权接口同一条边界: 运行期间不改运行配置。"""
    runtime = FakeRuntime()
    runtime.busy = True
    with _client(runtime, tmp_path) as client:
        headers = _csrf(client)
        assert (
            client.put(
                "/api/v1/models/current",
                json={"provider_id": "openai", "model_id": "gpt-x"},
                headers=headers,
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/v1/thinking", json={"mode": "on"}, headers=headers
            ).status_code
            == 409
        )
        assert (
            client.put(
                "/api/v1/model-overrides/act",
                json={"provider_id": "openai", "model_id": "gpt-x"},
                headers=headers,
            ).status_code
            == 409
        )


def test_provider_config_survives_web_serialization() -> None:
    """回归: ProviderConfig / ModelParams 从 dataclass 改成 Pydantic model 之后,
    `/api/v1/models` 整条端点炸在 to_jsonable 的"不支持的 Web DTO 类型"上.

    这个函数刻意认不出未知类型, 所以每换一种值对象的底座都要在这里登记一次 —— 这条
    用例就是那个提醒.
    """
    provider = ProviderConfig.model_validate(
        {
            "id": "deepseek",
            "name": "DeepSeek",
            "api_base": "https://example.invalid/chat/completions",
            "api_key_env": "DEEPSEEK_API_KEY",
            "models": (
                ModelSpec(
                    provider="deepseek",
                    id="deepseek-chat",
                    params=ModelParams.parse(
                        {"context_window": 64000, "extra": {"seed": 1}}
                    ),
                ),
            ),
        }
    )

    payload = to_jsonable(provider)

    assert payload["id"] == "deepseek"
    assert payload["timeout"] == 60
    assert payload["models"][0]["id"] == "deepseek-chat"
    assert payload["models"][0]["params"]["context_window"] == 64000
    assert payload["models"][0]["params"]["extra"] == {"seed": 1}
    # 整棵树必须是纯 JSON 值: 端点会把它交给 json 编码.
    json.dumps(payload)


def test_an_unknown_type_is_still_refused() -> None:
    """展开 Pydantic model 不能顺带把"认不出就抛"这条性质弄丢."""

    class _Opaque:
        pass

    with pytest.raises(TypeError):
        to_jsonable(_Opaque())


def test_one_axis_moves_without_touching_the_other(tmp_path) -> None:
    """正交性在接口上的样子: 调隔离档不该顺手把审批档也改了.

    早先两个轴压在一个四档枚举里, 这件事表达不出来 —— 想让 Shell 自动跑就必须同时
    放开网络与逐次裁决, 因为 full_access 是那条线上唯一带自动的位置.
    """
    from forgecli.domain.intents import ApprovalPolicy, SandboxLevel, SessionMode
    from forgecli.interfaces.web.app import ModeRequest, _resolve_mode

    current = SessionMode(SandboxLevel.WORKSPACE_WRITE, ApprovalPolicy.ALWAYS)

    only_sandbox = _resolve_mode(ModeRequest(sandbox="full_access"), current)
    assert only_sandbox.sandbox is SandboxLevel.FULL_ACCESS
    assert only_sandbox.approval is ApprovalPolicy.ALWAYS

    only_approval = _resolve_mode(ModeRequest(approval="never"), current)
    assert only_approval.sandbox is SandboxLevel.WORKSPACE_WRITE
    assert only_approval.approval is ApprovalPolicy.NEVER

    # 预设名仍然接受: 界面上那四个常用组合是一次点击的快捷方式.
    assert _resolve_mode(ModeRequest(mode="auto"), current) == SessionMode.AUTO


def test_a_combination_outside_the_presets_is_expressible(tmp_path) -> None:
    """强隔离 + 全自动: 这个组合在旧的四档线上没有位置."""
    from forgecli.domain.execution.fence import fence_for
    from forgecli.domain.intents import ApprovalPolicy, SandboxLevel, SessionMode

    stance = SessionMode(SandboxLevel.READ_ONLY, ApprovalPolicy.NEVER)
    fence = fence_for(stance, workspace_roots=("/ws",))

    assert fence.read_only is True
    assert fence.automatic_shell is True
    assert fence.network_allowed is False
