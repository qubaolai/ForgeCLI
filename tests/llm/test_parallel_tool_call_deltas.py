"""同一个流式 chunk 里的多个 tool call delta 必须全部保留.

这曾经是一个真 bug: adapter 只取 `delta.tool_calls[0]`, 而 OpenAI 兼容协议的
`delta.tool_calls` 本来就是数组. 模型一次请求两个工具时, 第二个连同它的 index 一起消失,
执行阶段自然也不会跑它 —— 模型却以为自己请求过, 下一轮拿到的是残缺的结果集.

所以断言的是"不丢", 不是"能显示": 这属于执行正确性, 不是终端展示 (ADR-0016 §5, 阶段 0).
"""

from __future__ import annotations

import json

from forgecli.application.llm.gateway.streaming import StreamAccumulator
from forgecli.domain.model.streaming import (
    ModelStreamChunk,
    ProviderStreamChunk,
    ToolCallDelta,
)
from forgecli.infrastructure.llm.adapters.openai_compatible import (
    OpenAICompatibleProvider,
)


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_id="fake", base_url="https://example.invalid"
    )


def _sse(delta: dict[str, object]) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


def _parse(provider: OpenAICompatibleProvider, line: str) -> ProviderStreamChunk:
    chunk = provider._parse_sse_line(line)  # noqa: SLF001 - 协议映射的最小可测单元
    assert chunk is not None
    return chunk


# ---- adapter 侧 ----


def test_one_chunk_with_two_tool_calls_keeps_both() -> None:
    chunk = _parse(
        _provider(),
        _sse(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_a",
                        "function": {"name": "fs.read_file", "arguments": '{"path"'},
                    },
                    {
                        "index": 1,
                        "id": "call_b",
                        "function": {"name": "search.text", "arguments": '{"query"'},
                    },
                ]
            }
        ),
    )
    assert [delta.index for delta in chunk.tool_call_deltas] == [0, 1]
    assert [delta.name for delta in chunk.tool_call_deltas] == [
        "fs.read_file",
        "search.text",
    ]


def test_missing_index_falls_back_to_array_position() -> None:
    # index 缺省时退回数组下标. 一律当 0 会让同 chunk 的多个调用在累积器里合成一个.
    chunk = _parse(
        _provider(),
        _sse(
            {
                "tool_calls": [
                    {"id": "a", "function": {"name": "one", "arguments": "{}"}},
                    {"id": "b", "function": {"name": "two", "arguments": "{}"}},
                ]
            }
        ),
    )
    assert [delta.index for delta in chunk.tool_call_deltas] == [0, 1]


def test_chunk_without_tool_calls_yields_empty_tuple() -> None:
    chunk = _parse(_provider(), _sse({"content": "hello"}))
    assert chunk.tool_call_deltas == ()
    assert chunk.delta_text == "hello"


# ---- 累积器侧 ----


def _chunk(sequence: int, *deltas: ToolCallDelta) -> ModelStreamChunk:
    return ModelStreamChunk(
        request_id="req-1",
        sequence=sequence,
        provider="fake",
        model="fake-model",
        tool_call_deltas=deltas,
    )


def test_accumulator_aggregates_two_calls_split_across_chunks() -> None:
    accumulator = StreamAccumulator()
    accumulator.add(
        _chunk(
            0,
            ToolCallDelta(index=0, tool_call_id="a", name="fs.read_file"),
            ToolCallDelta(index=1, tool_call_id="b", name="search.text"),
        )
    )
    accumulator.add(
        _chunk(
            1,
            ToolCallDelta(index=0, arguments_delta='{"path": "main.py"}'),
            ToolCallDelta(index=1, arguments_delta='{"query": "login"}'),
        )
    )

    calls = accumulator.tool_calls()
    assert [call.name for call in calls] == ["fs.read_file", "search.text"]
    assert dict(calls[0].arguments) == {"path": "main.py"}
    assert dict(calls[1].arguments) == {"query": "login"}
    assert not accumulator.has_partial_tool_calls()


def test_second_call_is_not_swallowed_by_the_first() -> None:
    """回归: 两个调用同处一个 chunk 时, 第二个不能被丢掉或并进第一个."""
    accumulator = StreamAccumulator()
    accumulator.add(
        _chunk(
            0,
            ToolCallDelta(index=0, tool_call_id="a", name="one", arguments_delta="{}"),
            ToolCallDelta(index=1, tool_call_id="b", name="two", arguments_delta="{}"),
        )
    )
    assert len(accumulator.tool_calls()) == 2
