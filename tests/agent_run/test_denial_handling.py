"""拒绝之后循环怎么走 (人机边界).

分流的判据是**谁说的不行**, 不是 mode:

- 人类点了 deny -> 本轮收工具. 他拒绝的是意图, 不是那一条命令; 允许换个说法重试等于
  让模型绕过刚做的决定, 审批提示就成了摆设.
- 策略 DENY -> 允许换路, 但计数. 换条合法的路正是我们希望它做的, 一直撞墙则不是.
- 无人可裁决 -> 立即收. 重试永远还是同一个结果.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from forgecli.application.tool_request.observations import (
    ObservationKind,
    ToolObservation,
)
from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopDecision,
    LoopObservation,
    LoopStop,
    ObservationDisposition,
    ToolRequestAction,
)
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.tool.tool_call import ToolCall
from support.loop_harness import (
    ScriptedGateway,
    call,
    loop_input,
    loop_with,
    response,
)


def _observation(kind: ObservationKind) -> LoopObservation:
    return ToolObservation(
        kind=kind, message="测试", invocation_id="inv-1", tool_name="shell_run"
    ).to_loop_observation()


# ---- 处置意见的归类 ----


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ObservationKind.TOOL_RESULT, ObservationDisposition.CONTINUE),
        (ObservationKind.PREPARATION_FAILED, ObservationDisposition.CONTINUE),
        (ObservationKind.POLICY_DENIED, ObservationDisposition.BLOCKED),
        (ObservationKind.APPROVAL_REQUIRED, ObservationDisposition.BLOCKED),
        (ObservationKind.RECOVERY_UNAVAILABLE, ObservationDisposition.BLOCKED),
        (ObservationKind.APPROVAL_DENIED, ObservationDisposition.HALT),
        (ObservationKind.APPROVAL_UNAVAILABLE, ObservationDisposition.HALT),
    ],
)
def test_kinds_map_to_the_right_disposition(
    kind: ObservationKind, expected: ObservationDisposition
) -> None:
    assert kind.disposition is expected


def test_a_model_mistake_is_retryable() -> None:
    """入参写错是模型自己的问题, 改对了重试是应该的 —— 不占拒绝预算."""
    assert (
        ObservationKind.PREPARATION_FAILED.disposition
        is ObservationDisposition.CONTINUE
    )


# ---- 人类拒绝 ----


def test_a_human_denial_closes_the_tool_catalog_for_the_turn() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("shell_run", "c1"),)),
            response("好的, 我原本想清理构建产物"),
        ]
    )
    loop, _ = loop_with(gateway)
    loop.start(loop_input())

    step = loop.observe(_observation(ObservationKind.APPROVAL_DENIED))

    # 模型被逼到只能作答, 而不是换个命令再试.
    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, AnswerAction)
    assert gateway.requests_tools == [3, 0], "第二次调用不该再带工具目录"


def test_the_model_is_told_not_to_rephrase() -> None:
    gateway = ScriptedGateway(
        responses=[response(tool_calls=(call("shell_run", "c1"),)), response("好")]
    )
    loop, _ = loop_with(gateway)
    loop.start(loop_input())
    loop.observe(_observation(ObservationKind.APPROVAL_DENIED))

    notices = [
        block.text
        for message in gateway.last_messages
        if message.role is MessageRole.USER
        for block in message.content
        if getattr(block, "text", "").startswith("本轮工具调用已停止")
    ]
    assert notices, "必须显式告诉模型停下"
    assert "绕过" in notices[0]


def test_nobody_to_approve_also_halts() -> None:
    """非交互环境下重试永远还是 pending, 空转烧 token."""
    gateway = ScriptedGateway(
        responses=[response(tool_calls=(call("shell_run", "c1"),)), response("好")]
    )
    loop, _ = loop_with(gateway)
    loop.start(loop_input())
    step = loop.observe(_observation(ObservationKind.APPROVAL_UNAVAILABLE))
    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, AnswerAction)


def test_queued_calls_still_get_a_tool_result() -> None:
    """协议要求每个 tool_call 都有对应结果; 直接丢掉排队项会让下一次请求残缺."""
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("shell_run", "c1"), call("fs_read", "c2"))),
            response("好"),
        ]
    )
    loop, _ = loop_with(gateway)
    loop.start(loop_input())
    loop.observe(_observation(ObservationKind.APPROVAL_DENIED))

    answered = {
        block.tool_call_id
        for message in gateway.last_messages
        if message.role is MessageRole.TOOL
        for block in message.content
        if hasattr(block, "tool_call_id")
    }
    assert answered == {"c1", "c2"}


# ---- 策略拒绝 ----


def _blocked_round(count: int) -> tuple[ScriptedGateway, object]:
    calls = [
        response(
            tool_calls=(
                ToolCall(
                    tool_call_id=f"c{index}",
                    name="shell_run",
                    # 每次换个写法: 重复调用闸的签名不同, 拦不住它.
                    arguments=MappingProxyType({"command": f"variant-{index}"}),
                ),
            )
        )
        for index in range(count)
    ]
    gateway = ScriptedGateway(responses=[*calls, response("我说明一下")])
    loop, _ = loop_with(gateway)
    loop.start(loop_input())
    return gateway, loop


def test_a_policy_denial_lets_the_model_try_another_way() -> None:
    gateway, loop = _blocked_round(2)
    step = loop.observe(_observation(ObservationKind.POLICY_DENIED))
    # 换条合法的路正是我们希望它做的.
    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, ToolRequestAction)


def test_repeated_policy_denials_eventually_stop_the_turn() -> None:
    gateway, loop = _blocked_round(3)
    for _ in range(3):
        step = loop.observe(_observation(ObservationKind.POLICY_DENIED))
    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, AnswerAction)
    assert gateway.requests_tools[-1] == 0


def test_rephrasing_does_not_reset_the_budget() -> None:
    """回归: 每次换不同的命令时, 重复调用闸放行 —— 拦住它的必须是拒绝预算."""
    gateway, loop = _blocked_round(3)
    for _ in range(3):
        loop.observe(_observation(ObservationKind.POLICY_DENIED))
    assert gateway.requests_tools.count(0) == 1, "第 3 次拒绝后就该收工具"


def test_a_success_between_denials_does_not_reset_the_counter() -> None:
    """数累计不数连续: 插一个无害读取就重置的话, 计数器永远到不了上限."""
    gateway, loop = _blocked_round(4)
    loop.observe(_observation(ObservationKind.POLICY_DENIED))
    loop.observe(_observation(ObservationKind.TOOL_RESULT))
    loop.observe(_observation(ObservationKind.POLICY_DENIED))
    step = loop.observe(_observation(ObservationKind.POLICY_DENIED))
    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, AnswerAction)


def test_a_single_human_denial_outweighs_the_whole_budget() -> None:
    """人类一次说不, 等价于预算直接清零 —— 不给"再试两次"的余地."""
    gateway, loop = _blocked_round(2)
    loop.observe(_observation(ObservationKind.APPROVAL_DENIED))
    assert gateway.requests_tools[-1] == 0, "一次拒绝就该收掉目录, 无需用满预算"


def test_asking_for_a_tool_after_the_catalog_closed_is_not_dispatched() -> None:
    """强制点在派发这里, 不在"没给你看你就不会要"上.

    模型幻觉出一个工具名, 或者供应商重放上一条 tool_call, 都会走到这里.
    """
    gateway, loop = _blocked_round(2)
    step = loop.observe(_observation(ObservationKind.APPROVAL_DENIED))
    assert isinstance(step, LoopStop)
    assert step.reason is LoopStopReason.POLICY_DENIED
