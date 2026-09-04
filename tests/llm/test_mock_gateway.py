"""按脚本回话的假网关 (--mock-llm).

它存在的意义是**把一条链路演给人看**: 工具调用, ask_user 那种要人回话再继续的往返,
流式渲染. 所以这一组盯的是"脚本说什么它就说什么", 以及三条不该把会话弄死的边界:
脚本用完, 脚本写坏, 用户中途取消.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forgecli.application.llm.gateway.streaming import StreamAccumulator
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams
from forgecli.domain.model.request import ModelRequest
from forgecli.domain.model.response import FinishReason
from forgecli.infrastructure.llm.mock_gateway import (
    MOCK_MODEL,
    MOCK_PROVIDER,
    MockLlmGateway,
    write_example_script,
)
from forgecli.shared.cancellation import CancelToken


def _request(cancel: CancelToken | None = None) -> ModelRequest:
    return ModelRequest(
        request_id="req-1",
        session_id="s-1",
        turn_id="t-1",
        origin=RequestOrigin.ACT,
        messages=(
            ChatMessage(role=MessageRole.USER, content=(TextBlock("帮我看看"),)),
        ),
        params=ModelParams(),
        cancel_token=cancel,
    )


def _script(tmp_path: Path, entries: object) -> Path:
    path = tmp_path / "mock.json"
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return path


# ---- 脚本说什么就说什么 ----


def test_responses_are_consumed_in_order(tmp_path: Path) -> None:
    gateway = MockLlmGateway(
        _script(tmp_path, [{"text": "第一句"}, {"text": "第二句"}])
    )
    assert gateway.complete(_request()).content == "第一句"
    assert gateway.complete(_request()).content == "第二句"


def test_a_tool_call_comes_back_as_a_tool_call(tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        [{"tool_calls": [{"name": "fs_find", "arguments": {"pattern": "*.py"}}]}],
    )
    response = MockLlmGateway(script).complete(_request())
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.name == "fs_find"
    assert call.arguments == {"pattern": "*.py"}
    # id 由位置派生: 同一条脚本重跑两次, 日志里的 id 也一样, 两次运行可以逐行对比.
    assert call.tool_call_id == "mock_0_0"


def test_text_and_a_tool_call_can_come_together(tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        [
            {
                "text": "先问一句.",
                "tool_calls": [
                    {"name": "ask_user", "arguments": {"question": "用哪个?"}}
                ],
            }
        ],
    )
    response = MockLlmGateway(script).complete(_request())
    assert response.content == "先问一句."
    assert response.tool_calls[0].name == "ask_user"


def test_the_identity_says_it_was_not_a_real_model(tmp_path: Path) -> None:
    """假装成 deepseek 会让事后翻日志的人得到一个错误的事实."""
    response = MockLlmGateway(_script(tmp_path, [{"text": "喂"}])).complete(_request())
    assert (response.provider, response.model) == (MOCK_PROVIDER, MOCK_MODEL)
    assert response.usage.estimated is True


@pytest.mark.parametrize(
    "payload",
    [
        [{"text": "裸数组"}],
        {"responses": [{"text": "裸数组"}], "_readme": ["多余的键一律忽略"]},
    ],
)
def test_both_top_level_shapes_are_accepted(tmp_path: Path, payload: object) -> None:
    assert MockLlmGateway(_script(tmp_path, payload)).complete(_request()).content == (
        "裸数组"
    )


# ---- 流式 ----


def test_streaming_reassembles_into_the_same_response(tmp_path: Path) -> None:
    """累积器拼回来的必须与脚本写的一字不差 —— 否则这个网关演的就不是脚本."""
    script = _script(
        tmp_path,
        [
            {
                "text": "这是一段够长的话, 会被切成好几块吐出来.",
                "tool_calls": [
                    {"name": "ask_user", "arguments": {"question": "继续吗?"}}
                ],
            }
        ],
    )
    accumulator = StreamAccumulator()
    chunks = list(MockLlmGateway(script).stream(_request()))
    for chunk in chunks:
        accumulator.add(chunk)

    assert len(chunks) > 2, "文本要分块吐, 否则看不出流式"
    assert accumulator.text == "这是一段够长的话, 会被切成好几块吐出来."
    assert accumulator.finish_reason is FinishReason.TOOL_CALLS
    assert accumulator.usage is not None
    calls = accumulator.tool_calls()
    assert calls[0].name == "ask_user"
    assert calls[0].arguments == {"question": "继续吗?"}


def test_the_last_chunk_carries_usage_and_finish_reason(tmp_path: Path) -> None:
    gateway = MockLlmGateway(_script(tmp_path, [{"text": "短"}]))
    chunks = list(gateway.stream(_request()))
    assert chunks[-1].usage_delta is not None
    assert chunks[-1].finish_reason is FinishReason.STOP


def test_a_cancelled_request_gets_a_closing_chunk_not_a_dead_stream(
    tmp_path: Path,
) -> None:
    """取消时收一个 interrupted 块, 而不是直接断流 (ADR-0011 §9)."""
    long_text = "很长的一段话" * 20
    cancel = CancelToken()
    cancel.cancel()
    chunks = list(
        MockLlmGateway(_script(tmp_path, [{"text": long_text}])).stream(
            _request(cancel)
        )
    )
    assert len(chunks) == 1
    assert chunks[0].interrupted is True
    assert chunks[0].finish_reason is FinishReason.USER_CANCELLED


# ---- 三条不该把会话弄死的边界 ----


def test_the_script_is_reread_on_every_call(tmp_path: Path) -> None:
    """一边跑一边改后面几条, 是这个网关最主要的用法."""
    script = _script(tmp_path, [{"text": "第一句"}, {"text": "旧的第二句"}])
    gateway = MockLlmGateway(script)
    assert gateway.complete(_request()).content == "第一句"
    script.write_text(
        json.dumps([{"text": "第一句"}, {"text": "新的第二句"}], ensure_ascii=False),
        encoding="utf-8",
    )
    assert gateway.complete(_request()).content == "新的第二句"


def test_an_exhausted_script_ends_the_turn_instead_of_looping(tmp_path: Path) -> None:
    """不循环也不重复最后一条: 那两种都会让一个写错的脚本变成转不完的循环."""
    gateway = MockLlmGateway(_script(tmp_path, [{"text": "唯一一句"}]))
    gateway.complete(_request())
    response = gateway.complete(_request())
    assert response.finish_reason is FinishReason.STOP
    assert response.tool_calls == ()
    assert "用完" in response.content


def test_a_broken_script_answers_instead_of_raising(tmp_path: Path) -> None:
    """编辑到一半的 JSON 很常见; 为它炸掉会话, 用户得重开一次并丢掉上下文."""
    script = tmp_path / "mock.json"
    script.write_text("{ 这不是 JSON", encoding="utf-8")
    response = MockLlmGateway(script).complete(_request())
    assert response.finish_reason is FinishReason.STOP
    assert "读不了" in response.content


def test_a_missing_script_answers_instead_of_raising(tmp_path: Path) -> None:
    response = MockLlmGateway(tmp_path / "nope.json").complete(_request())
    assert "读不了" in response.content


# ---- 示例脚本 ----


def test_the_example_script_actually_runs(tmp_path: Path) -> None:
    """`--mock-llm` 指向一个不存在的文件时写的就是它. 写完跑不了就是白写."""
    script = tmp_path / "nested" / "mock.json"
    write_example_script(script)
    gateway = MockLlmGateway(script)
    spoken = [gateway.complete(_request()) for _ in range(5)]
    assert any(item.tool_calls for item in spoken)
    assert any(
        call.name == "ask_user" for item in spoken for call in item.tool_calls
    ), "示例要演到 ask_user, 那是这个网关最主要的用途"
