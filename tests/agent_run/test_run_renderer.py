"""TerminalRunRenderer 的快照与安全约束 (ADR-0016 §8, §12.3, §12.4)."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    ApprovalRequestedPayload,
    ModelFailedPayload,
    PolicyResolvedPayload,
    RunEventPayload,
    RunPhase,
    StepStartedPayload,
    TextDeltaPayload,
    ToolCompletedPayload,
    ToolPreparedPayload,
    ToolQueuedPayload,
    TurnFinishedPayload,
)
from forgecli.interfaces.cli.run_renderer import TerminalRunRenderer

K = AgentRunEventKind


class Harness:
    def __init__(self) -> None:
        self.console = Console(
            file=io.StringIO(),
            width=100,
            record=True,
            no_color=True,
            force_terminal=False,
        )
        self.renderer = TerminalRunRenderer(self.console)
        self.bus = AgentRunEventBus()
        self.bus.subscribe(self.renderer)

    def send(self, kind: AgentRunEventKind, payload: RunEventPayload) -> None:
        self.bus.publish(kind, turn_id="turn-1", payload=payload)

    def text(self) -> str:
        return self.console.export_text()


@pytest.fixture
def harness() -> Harness:
    return Harness()


# ---- 正文提交 ----


def test_answer_text_is_committed_line_by_line(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(K.MODEL_OUTPUT_DELTA, TextDeltaPayload("第一行\n第二行"))
        harness.renderer.finish()
    output = harness.text()
    assert "第一行" in output
    assert "第二行" in output


def test_a_long_answer_appears_exactly_once(harness: Harness) -> None:
    """Live 只放半行, 完成的行提交到上方 —— 不能出现"擦除 + 重印"式的重复."""
    with harness.renderer.turn():
        for piece in ("很长的回答", "继续", "结束\n"):
            harness.send(K.MODEL_OUTPUT_DELTA, TextDeltaPayload(piece))
        harness.renderer.finish()
    assert harness.text().count("很长的回答继续结束") == 1


def test_rendered_text_tracks_what_was_committed(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(K.MODEL_OUTPUT_DELTA, TextDeltaPayload("答案"))
        harness.renderer.finish()
        assert harness.renderer.rendered_text == "答案"
        assert harness.renderer.had_output


# ---- 过程时间线 ----


def test_tool_call_and_result_land_on_the_timeline(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(K.TOOL_QUEUED, ToolQueuedPayload(tool_name="search.text"))
        harness.send(
            K.TOOL_COMPLETED,
            ToolCompletedPayload(
                tool_name="search.text",
                status="ok",
                elapsed_ms=84.0,
                result_summary="12 处匹配",
            ),
        )
        harness.renderer.finish()
    output = harness.text()
    assert "调用 search.text" in output
    assert "12 处匹配" in output
    assert "84 ms" in output


def test_prepared_shows_every_argument_verbatim(harness: Harness) -> None:
    """入参逐字显示, 不脱敏: 用户判断"要不要让这次调用发生"靠的就是它."""
    with harness.renderer.turn():
        harness.send(
            K.TOOL_PREPARED,
            ToolPreparedPayload(
                tool_name="search.text",
                arguments=(("path", "src"), ("query", "login")),
            ),
        )
        harness.renderer.finish()
    output = harness.text()
    assert 'query="login"' in output
    assert 'path="src"' in output


def test_side_effect_unknown_is_not_shown_as_a_plain_failure(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.TOOL_CANCELLED,
            ToolCompletedPayload(
                tool_name="shell.run", status="cancelled", side_effect_unknown=True
            ),
        )
        harness.renderer.finish()
    assert "副作用未知" in harness.text()


def test_turn_summary_reports_call_counts(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.TURN_COMPLETED,
            TurnFinishedPayload(
                status="final_answer",
                elapsed_ms=1800.0,
                model_calls=2,
                tool_calls=2,
            ),
        )
        harness.renderer.finish()
    output = harness.text()
    assert "2 次模型调用" in output
    assert "2 次工具调用" in output


def test_a_denied_policy_is_always_visible(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.POLICY_RESOLVED,
            PolicyResolvedPayload(
                tool_name="shell.run", decision="deny", reason="hard_deny"
            ),
        )
        harness.renderer.finish()
    assert "安全裁决 deny" in harness.text()


def test_model_failure_is_reported(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.MODEL_FAILED,
            ModelFailedPayload(error_kind="stream_interrupted", message="流式响应中断"),
        )
        harness.renderer.finish()
    assert "流式响应中断" in harness.text()


def test_control_sequences_in_an_argument_are_stripped(harness: Harness) -> None:
    """不脱敏不等于不清理: 参数里的 ANSI 序列会重画终端."""
    with harness.renderer.turn():
        harness.send(
            K.TOOL_PREPARED,
            ToolPreparedPayload(
                tool_name="shell.run",
                arguments=(("command", "rm -rf /tmp/x\x1b[2K\x1b[1A"),),
            ),
        )
        harness.renderer.finish()
    output = harness.text()
    assert "\x1b" not in output
    assert "rm -rf /tmp/x" in output


def test_allow_decisions_are_quiet(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.POLICY_RESOLVED,
            PolicyResolvedPayload(
                tool_name="fs.read_file", decision="allow", reason="fast_path"
            ),
        )
        harness.renderer.finish()
    assert "安全裁决" not in harness.text()


# ---- 安全 ----


def test_control_sequences_from_a_tool_name_are_stripped(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.TOOL_QUEUED, ToolQueuedPayload(tool_name="shell.run\x1b[2K\x1b[1A")
        )
        harness.renderer.finish()
    assert "\x1b" not in harness.text()


def test_rich_markup_in_a_result_is_escaped(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.TOOL_COMPLETED,
            ToolCompletedPayload(
                tool_name="x", status="ok", result_summary="[bold red]假的[/]"
            ),
        )
        harness.renderer.finish()
    assert "[bold red]假的[/]" in harness.text()


def test_an_absurdly_long_summary_is_capped(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.TOOL_COMPLETED,
            ToolCompletedPayload(tool_name="x", status="ok", result_summary="A" * 5000),
        )
        harness.renderer.finish()
    assert harness.text().count("A") < 1000


# ---- 审批交接 ----


def test_approval_takes_the_terminal_and_gives_it_back(harness: Harness) -> None:
    """审批期间活动区必须收掉, 否则 Live 会盖住输入提示 (§8.1)."""
    with harness.renderer.turn():
        harness.send(K.MODEL_OUTPUT_DELTA, TextDeltaPayload("我先看看"))
        harness.send(
            K.APPROVAL_REQUESTED,
            ApprovalRequestedPayload(tool_name="shell.run", mandatory=True),
        )
        # 交接时半行正文已经定稿提交, 不会留在 Live 里被审批提示覆盖.
        assert harness.renderer.rendered_text == "我先看看"
        harness.send(K.APPROVAL_RESOLVED, RunEventPayload())
        harness.renderer.finish()
    output = harness.text()
    assert "等待逐次批准" in output
    assert output.count("我先看看") == 1


# ---- 隔离 ----


def test_a_renderer_failure_does_not_break_the_bus(harness: Harness) -> None:
    # 没有 turn() 包裹时 refresh 是 no-op, 事件照常被吞下而不抛.
    harness.send(K.STEP_STARTED, StepStartedPayload(1, RunPhase.THINKING))
    assert not harness.bus.isolated_failures
