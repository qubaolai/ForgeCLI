"""模型产出的工具调用不可用时, 循环该怎么走.

区分两种"解析失败", 因为责任方不同, 收场方式也就不同:

- **模型的输出坏了** (参数不是完整 JSON, 或混进了 `<tool_call>` 一类 markup): 它改得了,
  前提是有人告诉它坏在哪. 追加一条纠错消息再给一次机会.
- **provider 的响应坏了** (HTTP body 压根不是 JSON): 不是模型的错, 告诉它是在冤枉它,
  重发同一条请求也不会好转. 直接中止.

这组用例钉住的是第一类. 它值得单独一个文件, 因为一次静默的格式失败代价极大: 见过真实
任务里 search_text 唯一一次调用就这么被打掉, 模型收不到任何反馈, 从此再没碰过这个工具,
全程改用 shell_run.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import MappingProxyType

from forgecli.application.llm.gateway.errors import (
    MalformedToolCallError,
    ModelResponseParseError,
)
from forgecli.application.tool_request.observations import (
    ObservationKind,
    ToolObservation,
)
from forgecli.domain.agent.actions import LoopDecision, LoopStop, ToolRequestAction
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.model.response import FinishReason
from forgecli.domain.model.streaming import ModelStreamChunk, ToolCallDelta
from forgecli.domain.tool.tool_call import ToolCall
from support.loop_harness import (
    ScriptedGateway,
    call,
    loop_input,
    loop_with,
    response,
)


def _dirty(call_id: str, value: str) -> ToolCall:
    return ToolCall(
        tool_call_id=call_id,
        name="shell_run",
        arguments=MappingProxyType({"command": value}),
    )


def _user_texts(gateway: ScriptedGateway) -> list[str]:
    """最后一次请求里所有 USER 消息的正文."""
    return [
        block.text
        for message in gateway.last_messages
        if message.role is MessageRole.USER
        for block in message.content
        if hasattr(block, "text")
    ]


# ---- 错误类型的分家 ----


def test_malformed_is_a_parse_error() -> None:
    """子类关系是这组行为的支点: 老的 except 分支不能因为新类型而漏掉这些错误."""
    assert issubclass(MalformedToolCallError, ModelResponseParseError)


# ---- markup 泄漏 ----


def test_markup_in_arguments_triggers_a_retry_not_a_dispatch() -> None:
    """参数里混进 markup 的调用一次都不派发, 而是回一条纠错消息重来.

    照原样派发的话, 它会一路走到安全裁决拿回 executable_not_found —— 那个结论把模型
    引向"我命令写错了", 而真正坏掉的是它的输出格式.
    """
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_dirty("c1", "find /x</think><tool_call>grep"),)),
            response(tool_calls=(call("search_text", "c2"),)),
        ]
    )
    loop, _ = loop_with(gateway)

    result = loop.start(loop_input())

    # 派发的是第二次那个干净的调用, 不是第一次那个脏的.
    assert isinstance(result, LoopDecision)
    action = result.next_action
    assert isinstance(action, ToolRequestAction)
    assert action.request.name == "search_text"
    assert len(gateway.responses) == 0


def test_the_retry_tells_the_model_what_broke() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_dirty("c1", "ls</think>"),)),
            response(tool_calls=(call("search_text", "c2"),)),
        ]
    )
    loop, _ = loop_with(gateway)

    loop.start(loop_input())

    notice = _user_texts(gateway)[-1]
    assert "</think>" in notice
    assert "tool_calls" in notice


def test_the_broken_call_never_enters_the_transcript() -> None:
    """带 tool_calls 的 assistant 消息一旦写进去, 就欠一份配对的 tool result.

    这批调用一个都不会执行, 所以那份 result 永远不会来, 下一次请求就是残缺的.
    """
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_dirty("c1", "<arg_value>rm"),)),
            response(tool_calls=(call("search_text", "c2"),)),
        ]
    )
    loop, _ = loop_with(gateway)

    loop.start(loop_input())

    assert all(not message.tool_calls for message in gateway.last_messages)


def test_markup_in_the_tool_name_counts_too() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(
                tool_calls=(
                    ToolCall(
                        tool_call_id="c1",
                        name="fs_list_files</think><tool_call>",
                        arguments=MappingProxyType({}),
                    ),
                )
            ),
            response(tool_calls=(call("search_text", "c2"),)),
        ]
    )
    loop, _ = loop_with(gateway)

    result = loop.start(loop_input())

    assert isinstance(result, LoopDecision)
    action = result.next_action
    assert isinstance(action, ToolRequestAction)
    assert action.request.name == "search_text"


def test_a_clean_call_is_not_mistaken_for_markup() -> None:
    """模型完全可能在写一段含 `<think>` 的 HTML. 判据只看工具名与字符串参数值."""
    gateway = ScriptedGateway(responses=[response(tool_calls=(call("fs_read", "c1"),))])
    loop, _ = loop_with(gateway)

    result = loop.start(loop_input())

    assert isinstance(result, LoopDecision)
    action = result.next_action
    assert isinstance(action, ToolRequestAction)
    assert action.request.name == "fs_read"


# ---- 重试上限 ----


def test_it_gives_up_after_two_retries() -> None:
    """两次纠错都没用就中止. 格式坏掉往往是持续性的, 无限重试会烧光整轮预算."""
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_dirty(f"c{index}", "x</think>"),))
            for index in range(3)
        ]
    )
    loop, _ = loop_with(gateway)

    result = loop.start(loop_input())

    assert isinstance(result, LoopStop)
    assert result.reason is LoopStopReason.MODEL_ERROR_BLOCKING
    # 三次响应全部消费掉了: 前两次触发重试, 第三次才放弃.
    assert len(gateway.responses) == 0


def test_the_count_is_cumulative_not_consecutive() -> None:
    """中间夹一次成功调用不重置计数.

    每隔一步坏一次的模型, 靠连续计数永远撞不到上限, 却能把整轮预算烧光. 这里的脚本是
    坏 -> 好 -> 坏 -> 坏: 连续计数会在第二段重新从 1 数起, 因而不会停; 累计计数在第三次
    损坏时到顶.
    """
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_dirty("c1", "a</think>"),)),
            response(tool_calls=(call("fs_read", "ok"),)),
            response(tool_calls=(_dirty("c2", "b</think>"),)),
            response(tool_calls=(_dirty("c3", "c</think>"),)),
        ]
    )
    loop, _ = loop_with(gateway)

    first = loop.start(loop_input())
    assert isinstance(first, LoopDecision)
    assert isinstance(first.next_action, ToolRequestAction)

    result = loop.observe(
        ToolObservation(
            kind=ObservationKind.TOOL_RESULT,
            message="读到了",
            invocation_id="ok",
            tool_name="fs_read",
        ).to_loop_observation()
    )

    assert isinstance(result, LoopStop)
    assert result.reason is LoopStopReason.MODEL_ERROR_BLOCKING
    assert len(gateway.responses) == 0


# ---- 流式的半截参数 ----


class TruncatedArgumentsGateway(ScriptedGateway):
    """第一次调用吐出半截 JSON 参数, 之后恢复正常.

    真实形态是 provider 把模型的工具调用切断在中途. 共享 harness 的 stream 永远产出
    完整 JSON, 所以这条分支只能在这里造出来.
    """

    def __init__(self, tail: ScriptedGateway) -> None:
        super().__init__(responses=list(tail.responses))
        self._emitted_broken = False

    def stream(self, request: object) -> Iterator[ModelStreamChunk]:  # type: ignore[override]
        if self._emitted_broken:
            return super().stream(request)  # type: ignore[arg-type]
        self._emitted_broken = True
        self._record(request)  # type: ignore[arg-type]
        self.responses.pop(0)
        return iter(
            (
                ModelStreamChunk(
                    request_id="req",
                    sequence=0,
                    provider="fake",
                    model="fake-model",
                    tool_call_deltas=(
                        ToolCallDelta(
                            index=0,
                            tool_call_id="c1",
                            name="shell_run",
                            # 半截: 引号没闭合, 对象没收尾.
                            arguments_delta='{"command": "find /x',
                        ),
                    ),
                ),
                ModelStreamChunk(
                    request_id="req",
                    sequence=1,
                    provider="fake",
                    model="fake-model",
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
        )


def test_partial_arguments_retry_instead_of_aborting_the_turn() -> None:
    """参数不完整不再直接判本轮死刑.

    仍然不替模型补参数 —— 补出来的是我们编的, 不是它要的. 这里只是把"不猜"和"不告诉
    它"分开: 只做前者.
    """
    gateway = TruncatedArgumentsGateway(
        ScriptedGateway(
            responses=[
                response(tool_calls=(call("shell_run", "c1"),)),
                response(tool_calls=(call("search_text", "c2"),)),
            ]
        )
    )
    loop, _ = loop_with(gateway)

    result = loop.start(loop_input())

    assert isinstance(result, LoopDecision)
    action = result.next_action
    assert isinstance(action, ToolRequestAction)
    assert action.request.name == "search_text"


def test_partial_arguments_are_never_guessed_into_shape() -> None:
    """纠错消息里不得出现任何被补全的参数值."""
    gateway = TruncatedArgumentsGateway(
        ScriptedGateway(
            responses=[
                response(tool_calls=(call("shell_run", "c1"),)),
                response(tool_calls=(call("search_text", "c2"),)),
            ]
        )
    )
    loop, _ = loop_with(gateway)

    loop.start(loop_input())

    notice = _user_texts(gateway)[-1]
    assert "find /x" not in notice
    assert json.dumps({"command": "find /x"}) not in notice
