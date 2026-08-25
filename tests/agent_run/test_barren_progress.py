"""连续多次工具调用没带回新信息时, 循环给一句提醒.

这道闸补的是 `_MAX_IDENTICAL_CALLS` 的盲区. 那道闸按**入参**判身份, 而一次真实任务里
8 个 grep 变体的参数各不相同 (加个 --include, 加个 | head, 换个转义), 全部放行, 返回的
却都是同一个空结果. 真正的浪费信号不是"参数一样", 是"结果没告诉我新东西".

只提醒, 不收工具: 空结果不是错误. 模型该做的是换思路或者直接报告"没找到", 这两件事都
还需要工具.
"""

from __future__ import annotations

from types import MappingProxyType

from forgecli.application.tool_request.observations import ObservationKind
from forgecli.domain.agent.actions import LoopDecision, LoopObservation
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.tool.tool_call import ToolCall
from support.loop_harness import (
    ScriptedGateway,
    loop_input,
    loop_with,
    response,
)

_NUDGE = "没有带回新信息"


def _observation(
    text: str, *, kind: ObservationKind = ObservationKind.TOOL_RESULT
) -> LoopObservation:
    """直接造一条观察. 内容与成败是这组用例仅有的两个输入."""
    return LoopObservation(
        content=text,
        source=kind.source,
        is_error=kind is not ObservationKind.TOOL_RESULT,
        disposition=kind.disposition,
    )


def _varied_call(index: int) -> ToolCall:
    """每次换个参数.

    参数恒定的话 _MAX_IDENTICAL_CALLS 会先把第三次挡下来, 这组用例就测不到自己想测的
    东西了 —— 而这恰恰是这道新闸存在的理由: 真实的原地打转从来不是同一份参数.
    """
    return ToolCall(
        tool_call_id=f"c{index}",
        name="search_text",
        arguments=MappingProxyType({"query": f"关键词{index}"}),
    )


def _drive(observations: list[LoopObservation]) -> ScriptedGateway:
    """每收到一条观察就再要一次工具, 把整串观察喂完."""
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_varied_call(index),))
            for index in range(len(observations) + 1)
        ]
    )
    loop, _ = loop_with(gateway)
    step = loop.start(loop_input())
    assert isinstance(step, LoopDecision)
    for observation in observations:
        loop.observe(observation)
    return gateway


def _results(texts: list[str]) -> list[LoopObservation]:
    return [_observation(text) for text in texts]


def _user_texts(gateway: ScriptedGateway) -> list[str]:
    return [
        block.text
        for message in gateway.last_messages
        if message.role is MessageRole.USER
        for block in message.content
        if hasattr(block, "text")
    ]


def _nudges(gateway: ScriptedGateway) -> list[str]:
    return [text for text in _user_texts(gateway) if _NUDGE in text]


# ---- 触发 ----


def test_three_empty_results_earn_a_nudge() -> None:
    assert _nudges(_drive(_results(["", "", ""])))


def test_three_identical_results_earn_a_nudge() -> None:
    """内容一样就算没有新信息, 哪怕它不为空.

    trace 里的 grep 变体正是这个形状: 命令各不相同, 输出一模一样.
    """
    assert _nudges(_drive(_results(["同一段输出"] * 4)))


def test_two_are_not_enough() -> None:
    """阈值是 3. 连着两次空结果在正常探索里太常见, 提醒得太早只是噪音."""
    assert not _nudges(_drive(_results(["", ""])))


def test_a_real_result_resets_the_streak() -> None:
    """中间拿到过东西就不算原地打转."""
    assert not _nudges(_drive(_results(["", "", "找到了 auth.py", "", ""])))


def test_it_does_not_fire_again_on_every_later_step() -> None:
    """提醒之后计数清零, 否则之后每一步都再提醒一次, 提醒本身变成噪音."""
    assert len(_nudges(_drive(_results(["", "", "", "", ""])))) == 1


def test_the_nudge_says_that_not_finding_is_itself_a_conclusion() -> None:
    """提醒要给出下一步, 不能只说"你在原地打转"."""
    notice = _nudges(_drive(_results(["", "", ""])))[0]

    assert "找不到本身就是结论" in notice


# ---- 与其它闸的分工 ----


def test_failures_do_not_count_here() -> None:
    """被拒绝的调用有专门的闸 (_MAX_BLOCKED_CALLS 与 HALT).

    两个计数器数同一件事, 事后就说不清到底是哪条规则停的.
    """
    denied = [_observation("", kind=ObservationKind.POLICY_DENIED) for _ in range(3)]

    assert not _nudges(_drive(denied))


def test_tools_stay_open_after_a_nudge() -> None:
    """空结果不是错误. 换思路和"直接报告没找到"都还需要工具."""
    gateway = _drive(_results(["", "", ""]))

    assert gateway.requests_tools[-1] > 0
