"""开发者诊断读模型与 /diagnostics (ADR-0035 决策 6).

CLI 与 Web 读同一个函数: 两个入口给出不同的数字, 排查时就得先判断该信哪一个.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from forgecli.application.llm.gateway.observability import (
    GatewayCallSample,
    InProcessGatewayMetrics,
)
from forgecli.domain.intents import SlashCommand
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.response import FinishReason
from forgecli.interfaces.cli.commands.diagnostics_command import DiagnosticsCommand
from forgecli.interfaces.runtime.diagnostics import (
    diagnostics_report,
    render_diagnostics,
)
from forgecli.shared.observability.configure import configure_logging
from forgecli.shared.observability.metrics import METRICS


@pytest.fixture(autouse=True)
def _collect_metrics() -> Iterator[None]:
    """采集默认是关的 (配置项 telemetry.enabled). 这些用例问的是开着时的行为."""
    METRICS.set_enabled(True)
    try:
        yield
    finally:
        METRICS.set_enabled(False)


class _Output:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, text: str) -> None:
        self.lines.append(text)


def _command(*args: str) -> SlashCommand:
    raw = " ".join(("/diagnostics", *args))
    return SlashCommand(raw, "diagnostics", args)


def test_report_names_the_log_file(tmp_path: Path) -> None:
    """排查的第一步永远是先找到日志文件, 所以它必须在这份读数里."""
    status = configure_logging(directory=tmp_path, level="debug")

    logging_view = diagnostics_report()["logging"]
    assert isinstance(logging_view, dict)
    assert logging_view["configured"] is True
    assert logging_view["level"] == "debug"
    assert logging_view["file"] == str(status.file)


def test_report_carries_stage_durations() -> None:
    METRICS.reset()
    METRICS.observe("tool.execute", 12.5, outcome="ok")

    stages = diagnostics_report()["stages"]
    assert isinstance(stages, dict)
    assert "tool.execute{outcome=ok}" in stages["durations"]


def test_gateway_section_appears_only_with_a_gateway() -> None:
    assert "gateway" not in diagnostics_report()

    metrics = InProcessGatewayMetrics()
    metrics.on_call(
        GatewayCallSample(
            provider="openai",
            model="gpt-4o",
            origin=RequestOrigin.ACT,
            finish_reason=FinishReason.STOP,
            latency_ms=420.0,
        )
    )
    gateway = diagnostics_report(metrics)["gateway"]
    assert isinstance(gateway, dict)
    assert gateway["openai/gpt-4o"]["calls"] == 1


def test_render_mentions_the_unconfigured_case() -> None:
    """没装配过日志时不能只显示一个空的 level: 那看起来像装配了但没写东西."""
    text = render_diagnostics({"process": {}, "logging": {"configured": False}})
    assert "未装配" in text


def test_command_prints_the_report(tmp_path: Path) -> None:
    configure_logging(directory=tmp_path, level="info")
    output = _Output()

    changed = DiagnosticsCommand(output).execute(_command())

    assert changed is False  # 纯查看
    assert "日志文件" in output.lines[0]


def test_reset_clears_counters_but_not_the_log(tmp_path: Path) -> None:
    configure_logging(directory=tmp_path, level="info")
    METRICS.observe("tool.execute", 1.0)
    output = _Output()

    DiagnosticsCommand(output).execute(_command("--reset"))

    assert METRICS.snapshot() == {"counters": {}, "durations": {}}
    assert "已清空" in output.lines[0]
