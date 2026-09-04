"""终端时间线的渲染判据 (ADR-0045).

这一组查的都是"画错了也不会有任何东西报错"的那类缺陷: 一次没执行过的调用在终端上
一行都不留, 一次被打断的写入被显示成普通失败, 或者模型正文和结构化行挤在同一行里.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from forgecli.domain.agent.run_events import (
    AgentRunEvent,
    AgentRunEventKind,
    ModelUsagePayload,
    PolicyResolvedPayload,
    ReasoningStatus,
    ReasoningStatusPayload,
    TextDeltaPayload,
    ToolCompletedPayload,
    ToolPreparedPayload,
    ToolStartedPayload,
    TurnFinishedPayload,
)
from forgecli.interfaces.tui.run_view import RunEventCollector, TerminalRunView


@pytest.fixture
def screen() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def view(screen: io.StringIO) -> TerminalRunView:
    # 固定宽度且关掉颜色: 断言的是内容, 不是这台机器的终端有多宽.
    return TerminalRunView(
        Console(file=screen, width=100, no_color=True, highlight=False)
    )


def _event(
    kind: AgentRunEventKind,
    payload: object,
    *,
    sequence: int = 1,
    tool_call_id: str | None = None,
) -> AgentRunEvent:
    return AgentRunEvent(
        event_id=f"e{sequence}",
        kind=kind,
        turn_id="turn_1",
        sequence=sequence,
        occurred_at=0.0,
        payload=payload,  # type: ignore[arg-type]
        tool_call_id=tool_call_id,
    )


def test_model_text_is_streamed_verbatim(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """增量逐段落地, 不重排也不加前缀.

    Rich 默认按自己算出的宽度断行, 而两次 print 之间它并不知道上一段停在第几列 ——
    没有 soft_wrap 的话, 一句话会被切成一列宽的碎片.
    """
    for index, chunk in enumerate(["现在", "开始", "干活"], start=1):
        view.handle(
            _event(
                AgentRunEventKind.MODEL_OUTPUT_DELTA,
                TextDeltaPayload(chunk),
                sequence=index,
            )
        )
    assert "现在开始干活" in screen.getvalue()


def test_structured_line_closes_the_streamed_line(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """结构化行之前必须先收掉正文那一行, 否则工具名会接在半句话后面."""
    view.handle(
        _event(AgentRunEventKind.MODEL_OUTPUT_DELTA, TextDeltaPayload("先读一下"))
    )
    view.handle(
        _event(
            AgentRunEventKind.TOOL_STARTED, ToolStartedPayload("fs_read"), sequence=2
        )
    )
    lines = [line.strip() for line in screen.getvalue().splitlines() if line.strip()]
    assert "先读一下" in lines
    assert any(line.endswith("fs_read") for line in lines)


def test_prepared_arguments_show_up_on_the_tool_line(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """入参逐字给到时间线: 用户判断"它在干什么"只有这一行可看."""
    view.handle(
        _event(
            AgentRunEventKind.TOOL_PREPARED,
            ToolPreparedPayload("shell_run", arguments=(("command", "pytest -q"),)),
            tool_call_id="call_1",
        )
    )
    view.handle(
        _event(
            AgentRunEventKind.TOOL_STARTED,
            ToolStartedPayload("shell_run"),
            sequence=2,
            tool_call_id="call_1",
        )
    )
    assert "command=pytest -q" in screen.getvalue()


def test_a_call_that_never_executed_still_gets_a_line(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """没等到批准的调用不发 TOOL_STARTED.

    时间线上必须自己补一行 —— 否则一次被拒的调用在终端上什么都不会留下, 而模型下一步
    的行为正是被它决定的.
    """
    view.handle(
        _event(
            AgentRunEventKind.TOOL_COMPLETED,
            ToolCompletedPayload(
                "shell_run", status="denied", error_summary="用户拒绝", executed=False
            ),
            tool_call_id="call_1",
        )
    )
    output = screen.getvalue()
    assert "shell_run" in output
    assert "用户拒绝" in output


def test_interrupted_write_is_not_reported_as_a_plain_failure(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """side_effect_unknown: "没成功"与"没发生"是两回事 (ADR-0016 §10.1)."""
    view.handle(
        _event(
            AgentRunEventKind.TOOL_COMPLETED,
            ToolCompletedPayload(
                "shell_run",
                status="error",
                error_summary="被取消",
                side_effect_unknown=True,
            ),
        )
    )
    assert "已执行部分未知" in screen.getvalue()


def test_auto_allowed_decisions_do_not_interrupt_the_timeline(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """自动放行不占一行; 要人点头或直接拒了的必须看得见."""
    view.handle(
        _event(
            AgentRunEventKind.POLICY_RESOLVED,
            PolicyResolvedPayload("fs_read", decision="allow", reason="只读"),
        )
    )
    assert screen.getvalue().strip() == ""
    view.handle(
        _event(
            AgentRunEventKind.POLICY_RESOLVED,
            PolicyResolvedPayload(
                "shell_run",
                decision="deny",
                reason="hard_deny",
                risk_facts=("删除文件",),
            ),
            sequence=2,
        )
    )
    output = screen.getvalue()
    assert "shell_run" in output
    assert "删除文件" in output


def test_usage_accumulates_across_calls_and_marks_estimates(
    view: TerminalRunView,
) -> None:
    """估算值必须留着标记: 当成事实展示, 用户会拿它去核账单."""
    view.handle(
        _event(
            AgentRunEventKind.MODEL_USAGE,
            ModelUsagePayload(origin="act", total_tokens=100),
        )
    )
    view.handle(
        _event(
            AgentRunEventKind.MODEL_USAGE,
            ModelUsagePayload(origin="act", total_tokens=40, estimated=True),
            sequence=2,
        )
    )
    assert view.metrics.total_tokens == 140
    assert view.metrics.estimated


def test_compaction_usage_is_called_out(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """压缩烧掉的那一笔不发 MODEL_STARTED; 不标出来, 合计就对不上模型调用次数."""
    view.handle(
        _event(
            AgentRunEventKind.MODEL_USAGE,
            ModelUsagePayload(origin="compact", total_tokens=900),
        )
    )
    assert "压缩用掉 900" in screen.getvalue()


def test_reasoning_reports_status_only(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """只报"在想", 绝不从回答或 token 数倒推一段思维链 (ADR-0016 §6)."""
    view.handle(
        _event(
            AgentRunEventKind.MODEL_REASONING_STATUS,
            ReasoningStatusPayload(ReasoningStatus.STARTED),
        )
    )
    assert "思考中" in screen.getvalue()


def test_turn_metrics_land_on_the_final_line(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    view.handle(
        _event(
            AgentRunEventKind.MODEL_USAGE,
            ModelUsagePayload(origin="act", total_tokens=1234),
        )
    )
    view.handle(
        _event(
            AgentRunEventKind.TURN_COMPLETED,
            TurnFinishedPayload(
                status="completed", elapsed_ms=2500, model_calls=2, tool_calls=3
            ),
            sequence=2,
        )
    )
    output = screen.getvalue()
    assert view.finished
    assert "2.5s" in output
    assert "模型 2 次" in output
    assert "工具 3 次" in output
    assert "1234 tokens" in output


def test_cancelled_turn_says_so(view: TerminalRunView, screen: io.StringIO) -> None:
    view.handle(
        _event(
            AgentRunEventKind.TURN_CANCELLED,
            TurnFinishedPayload(status="cancelled", detail="用户停止"),
        )
    )
    assert "已停止" in screen.getvalue()


def test_collector_hands_events_over_once() -> None:
    """收集器只管收: 渲染必须回到主线程, 两个线程同时 print 会把审批卡片搅碎."""
    collector = RunEventCollector()
    collector.on_event(
        _event(AgentRunEventKind.TOOL_STARTED, ToolStartedPayload("fs_read"))
    )
    assert len(collector.drain()) == 1
    assert collector.drain() == ()


def test_the_turn_opens_with_a_blank_line(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """过程与用户刚说的那句话之间要有一行空.

    紧贴着排, 两个人说的话在滚动历史里看起来像同一段.
    """
    view.handle(_event(AgentRunEventKind.TOOL_STARTED, ToolStartedPayload("fs_read")))
    assert screen.getvalue().startswith("\n")


def test_only_one_blank_line_opens_the_turn(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """开场那一行只留一次, 不是每种事件各留一次."""
    view.handle(
        _event(
            AgentRunEventKind.MODEL_REASONING_STATUS,
            ReasoningStatusPayload(ReasoningStatus.STARTED),
        )
    )
    view.handle(
        _event(
            AgentRunEventKind.TOOL_STARTED, ToolStartedPayload("fs_read"), sequence=2
        )
    )
    assert not screen.getvalue().startswith("\n\n")


def test_the_answer_is_separated_from_the_process(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """答案与工具行贴在一起时, 用户要先分辨哪一行才是回答."""
    view.handle(_event(AgentRunEventKind.TOOL_STARTED, ToolStartedPayload("fs_read")))
    view.handle(
        _event(
            AgentRunEventKind.TOOL_COMPLETED,
            ToolCompletedPayload("fs_read", status="ok", result_summary="读了 12 行"),
            sequence=2,
        )
    )
    view.handle(
        _event(
            AgentRunEventKind.MODEL_OUTPUT_DELTA, TextDeltaPayload("改好了"), sequence=3
        )
    )
    lines = screen.getvalue().splitlines()
    assert lines[lines.index("改好了") - 1].strip() == ""


def test_metrics_get_their_own_line_break(
    view: TerminalRunView, screen: io.StringIO
) -> None:
    """读数是这一轮的落款, 不是回答的最后一句."""
    view.handle(
        _event(AgentRunEventKind.MODEL_OUTPUT_DELTA, TextDeltaPayload("改好了"))
    )
    view.handle(
        _event(
            AgentRunEventKind.TURN_COMPLETED,
            TurnFinishedPayload(status="completed", elapsed_ms=1200),
            sequence=2,
        )
    )
    lines = [line for line in screen.getvalue().splitlines()]
    metrics = next(index for index, line in enumerate(lines) if "1.2s" in line)
    assert lines[metrics - 1].strip() == ""
