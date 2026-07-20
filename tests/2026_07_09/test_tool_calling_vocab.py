"""tool calling / tool result 统一词汇与 delta 累积（ADR-0011 §9 / §10）。"""

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    ModelResponseParseError,
    ModelStreamChunk,
    StreamAccumulator,
    TextBlock,
    ToolCall,
    ToolCallDelta,
    ToolResultBlock,
)
from forgecli.domain.conversation import MessageRole


def _chunk(sequence: int, delta: ToolCallDelta) -> ModelStreamChunk:
    return ModelStreamChunk(
        request_id="req_1", sequence=sequence, tool_call_delta=delta
    )


def test_tool_role_exists_for_tool_result_messages() -> None:
    assert MessageRole.TOOL.value == "tool"


def test_tool_result_block_links_by_tool_call_id() -> None:
    block = ToolResultBlock(tool_call_id="call_1", content="file contents")
    message = ChatMessage(role=MessageRole.TOOL, content=(block,))
    assert isinstance(message.content[0], ToolResultBlock)
    assert message.content[0].tool_call_id == "call_1"


def test_tool_result_block_requires_tool_call_id() -> None:
    with pytest.raises(ValueError):
        ToolResultBlock(tool_call_id="  ", content="x")


def test_assistant_message_may_carry_tool_calls_without_text() -> None:
    call = ToolCall(tool_call_id="call_1", name="read_file", arguments={"path": "a"})
    message = ChatMessage(role=MessageRole.ASSISTANT, content=(), tool_calls=(call,))
    assert message.tool_calls == (call,)


def test_message_still_requires_content_or_tool_calls() -> None:
    with pytest.raises(ValueError):
        ChatMessage(role=MessageRole.ASSISTANT, content=())


def test_plain_text_message_unchanged() -> None:
    message = ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),))
    assert message.tool_calls == ()


# ---- tool call delta 累积（§9）----


def test_tool_call_delta_accumulates_into_normalized_tool_call() -> None:
    acc = StreamAccumulator()
    acc.add(_chunk(0, ToolCallDelta(index=0, tool_call_id="call_9", name="grep")))
    acc.add(_chunk(1, ToolCallDelta(index=0, arguments_delta='{"pattern": ')))
    acc.add(_chunk(2, ToolCallDelta(index=0, arguments_delta='"foo"}')))
    calls = acc.tool_calls()
    assert calls == (
        ToolCall(tool_call_id="call_9", name="grep", arguments={"pattern": "foo"}),
    )


def test_multiple_tool_calls_keep_index_order() -> None:
    acc = StreamAccumulator()
    acc.add(_chunk(0, ToolCallDelta(index=1, tool_call_id="b", name="second")))
    acc.add(_chunk(1, ToolCallDelta(index=0, tool_call_id="a", name="first")))
    names = [call.name for call in acc.tool_calls()]
    assert names == ["first", "second"]


def test_incomplete_arguments_raise_parse_error() -> None:
    acc = StreamAccumulator()
    acc.add(_chunk(0, ToolCallDelta(index=0, name="grep", arguments_delta='{"a": ')))
    with pytest.raises(ModelResponseParseError):
        acc.tool_calls()
    assert acc.has_partial_tool_calls() is True


def test_non_object_arguments_rejected() -> None:
    acc = StreamAccumulator()
    acc.add(_chunk(0, ToolCallDelta(index=0, name="grep", arguments_delta="[1, 2]")))
    with pytest.raises(ModelResponseParseError):
        acc.tool_calls()


def test_no_partial_when_all_calls_complete() -> None:
    acc = StreamAccumulator()
    acc.add(_chunk(0, ToolCallDelta(index=0, name="grep", arguments_delta="{}")))
    assert acc.has_partial_tool_calls() is False
