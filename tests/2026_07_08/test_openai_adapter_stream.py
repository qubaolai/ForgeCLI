"""OpenAICompatibleProvider.stream：SSE 解析与取消中止（ADR-0011 §9）。"""

import httpx
import pytest

from forgecli.application.llm.gateway import (
    CancelToken,
    Credential,
    FinishReason,
    ModelAuthError,
    ModelCancelledError,
    ModelParams,
    TextBlock,
)
from forgecli.application.llm.gateway.messages import ChatMessage
from forgecli.application.llm.gateway.provider import ProviderRequest
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import OpenAICompatibleProvider

_BASE = "https://fake.api.test"


def _sse(*events: str) -> str:
    return "".join(f"data: {event}\n\n" for event in events)


def _provider(handler) -> OpenAICompatibleProvider:  # noqa: ANN001
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleProvider(
        provider_id="deepseek",
        base_url=_BASE,
        client_factory=lambda: httpx.Client(transport=transport),
    )


def _request(cancel_token: CancelToken | None = None) -> ProviderRequest:
    return ProviderRequest(
        model="deepseek-chat",
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
        cancel_token=cancel_token,
        credential=Credential(provider_id="deepseek", ref="FAKE", value="fk"),
    )


def test_stream_parses_text_deltas_usage_and_finish() -> None:
    body = _sse(
        '{"choices": [{"delta": {"content": "你"}}]}',
        '{"choices": [{"delta": {"content": "好"}}]}',
        '{"choices": [{"delta": {}, "finish_reason": "stop"}]}',
        '{"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 2, '
        '"total_tokens": 4}}',
        "[DONE]",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert b'"stream": true' in request.content or b'"stream":true' in (
            request.content.replace(b" ", b"")
        )
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )

    chunks = list(_provider(handler).stream(_request()))
    text = "".join(chunk.delta_text or "" for chunk in chunks)
    assert text == "你好"
    finishes = [chunk.finish_reason for chunk in chunks if chunk.finish_reason]
    assert finishes == [FinishReason.STOP]
    usages = [chunk.usage for chunk in chunks if chunk.usage is not None]
    assert usages and usages[-1].total_tokens == 4


def test_stream_parses_tool_call_deltas() -> None:
    body = _sse(
        '{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", '
        '"function": {"name": "grep", "arguments": ""}}]}}]}',
        '{"choices": [{"delta": {"tool_calls": [{"index": 0, '
        '"function": {"arguments": "{\\"p\\": 1}"}}]}}]}',
        "[DONE]",
    )
    chunks = list(
        _provider(lambda _req: httpx.Response(200, text=body)).stream(_request())
    )
    deltas = [chunk.tool_call_delta for chunk in chunks if chunk.tool_call_delta]
    assert deltas[0] is not None and deltas[0].tool_call_id == "call_1"
    assert deltas[0].name == "grep"
    assert deltas[1] is not None and deltas[1].arguments_delta == '{"p": 1}'


def test_cancel_mid_stream_aborts_connection() -> None:
    token = CancelToken()
    body = _sse(
        '{"choices": [{"delta": {"content": "第一段"}}]}',
        '{"choices": [{"delta": {"content": "第二段"}}]}',
        "[DONE]",
    )
    stream = _provider(lambda _req: httpx.Response(200, text=body)).stream(
        _request(cancel_token=token)
    )
    first = next(stream)
    assert first.delta_text == "第一段"
    token.cancel()
    with pytest.raises(ModelCancelledError):
        next(stream)


def test_pre_cancelled_stream_never_sends_request() -> None:
    called = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["count"] += 1
        return httpx.Response(200, text=_sse("[DONE]"))

    token = CancelToken()
    token.cancel()
    with pytest.raises(ModelCancelledError):
        list(_provider(handler).stream(_request(cancel_token=token)))
    assert called["count"] == 0


def test_stream_http_error_normalized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad"}})

    with pytest.raises(ModelAuthError):
        list(_provider(handler).stream(_request()))
