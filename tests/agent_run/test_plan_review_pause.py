"""工具声明"需要人裁决"时, 循环在回合边界停下 (ADR-0023 决策 1).

链条上每一环**只看字段, 不看名字**:

    ToolResult.turn_disposition = AWAIT_USER_DECISION
      -> ObservationKind.PLAN_REVIEW_REQUIRED
      -> ObservationDisposition.AWAIT_USER_DECISION
      -> LoopStopReason.WAIT_PLAN_REVIEW (RESUMABLE_PAUSE)

这组用例最要紧的一条是最后那个: **循环实现里不出现任何具体工具名**. 出现了, 这套机制
就只为 plan.write 服务, 将来的 ask_user 一类工具得再改一次循环.
"""

from __future__ import annotations

import inspect

import pytest

from forgecli.application.agent_loop import builtin_loop
from forgecli.application.tool_request.observations import (
    ObservationKind,
    ToolObservation,
)
from forgecli.domain.agent.actions import (
    LoopDecision,
    LoopStop,
    ObservationDisposition,
)
from forgecli.domain.agent.stop import LoopStopReason, StopClassification
from forgecli.domain.tool.result import (
    ContentPart,
    ToolError,
    ToolResult,
    ToolResultStatus,
    TurnDisposition,
)
from support.loop_harness import (
    ScriptedGateway,
    call,
    loop_input,
    loop_with,
    response,
)


def _review_observation() -> object:
    result = ToolResult(
        invocation_id="inv-1",
        tool_name="plan.write",
        status=ToolResultStatus.OK,
        content_parts=(ContentPart(text="# 一份计划"),),
        turn_disposition=TurnDisposition.AWAIT_USER_DECISION,
    )
    return ToolObservation(
        kind=ObservationKind.PLAN_REVIEW_REQUIRED,
        message="ok",
        invocation_id="inv-1",
        tool_name="plan.write",
        result=result,
    ).to_loop_observation()


# ---- 词汇分类 ----


def test_the_kind_maps_to_await_user_decision() -> None:
    assert (
        ObservationKind.PLAN_REVIEW_REQUIRED.disposition
        is ObservationDisposition.AWAIT_USER_DECISION
    )


def test_a_review_observation_is_not_an_error() -> None:
    """工具成功了. 分成独立的 kind 只是为了让循环按字段分流."""
    assert not _review_observation().is_error  # type: ignore[attr-defined]


def test_the_review_content_is_the_tool_output() -> None:
    """人要看的是计划正文本身, 不是一句"有个计划等你看"."""
    assert "# 一份计划" in _review_observation().content  # type: ignore[attr-defined]


def test_the_stop_reason_is_a_resumable_pause() -> None:
    """会话还能继续, 只是这一轮结束了. 归 BLOCKING 会让终端显示成失败."""
    assert (
        LoopStopReason.WAIT_PLAN_REVIEW.classification
        is StopClassification.RESUMABLE_PAUSE
    )


# ---- 循环行为 ----


def test_the_loop_stops_at_the_turn_boundary() -> None:
    gateway = ScriptedGateway(
        responses=[response(tool_calls=(call("plan.write", "c1"),))]
    )
    loop, _ = loop_with(gateway)
    step = loop.start(loop_input())
    assert isinstance(step, LoopDecision)

    stop = loop.observe(_review_observation())  # type: ignore[arg-type]

    assert isinstance(stop, LoopStop)
    assert stop.reason is LoopStopReason.WAIT_PLAN_REVIEW


def test_the_loop_does_not_call_the_model_again() -> None:
    """模型已经把要说的说完了 —— 它交出了一份计划, 正等着回话.

    再跑一轮只会在评审界面上方多出一段没人读的文字.
    """
    gateway = ScriptedGateway(
        responses=[response(tool_calls=(call("plan.write", "c1"),))]
    )
    loop, _ = loop_with(gateway)
    loop.start(loop_input())

    loop.observe(_review_observation())  # type: ignore[arg-type]

    assert gateway.responses == [], "脚本里只该消费掉第一条"
    assert len(gateway.requests) == 1


# ---- 机制不绑定任何工具 ----


def test_the_loop_never_names_a_planning_tool() -> None:
    """出现了工具名, 这套机制就只为 plan.write 服务.

    将来的 ask_user 一类工具得再改一次循环, 而循环恰恰是最不该认识工具语义的一层.
    """
    source = inspect.getsource(builtin_loop)

    for name in ("plan.write", "plan.read", "todo.write", "todo.set_status"):
        assert name not in source


def test_a_plain_result_still_continues() -> None:
    """没声明的工具照常继续. 停顿是工具主动要的, 不是默认行为."""
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs.read_file", "c1"),)),
            response(text="读完了"),
        ]
    )
    loop, _ = loop_with(gateway)
    loop.start(loop_input())

    step = loop.observe(
        ToolObservation(
            kind=ObservationKind.TOOL_RESULT,
            message="ok",
            invocation_id="inv-1",
            tool_name="fs.read_file",
        ).to_loop_observation()
    )

    assert isinstance(step, LoopDecision)


# ---- 失效方向 ----


def test_a_failed_result_cannot_ask_for_a_decision() -> None:
    """失败的调用没有可供人裁决的产出, 停下只会给人一个空的评审界面."""
    with pytest.raises(ValueError, match="只有成功的结果"):
        ToolResult(
            invocation_id="inv-1",
            tool_name="plan.write",
            status=ToolResultStatus.TOOL_ERROR,
            error=ToolError(code="boom", message="失败了"),
            turn_disposition=TurnDisposition.AWAIT_USER_DECISION,
        )
