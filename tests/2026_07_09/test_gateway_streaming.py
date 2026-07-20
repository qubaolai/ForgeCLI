"""gateway.stream：chunk 归一化、收尾汇总、取消中断（ADR-0011 §9）。"""

import pytest

from forgecli.application.llm.gateway import (
    CancelToken,
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    FinishReason,
    InMemoryModelCatalog,
    ModelBadRequestError,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ModelUsage,
    ProviderCapabilities,
    ProviderRegistry,
    ProviderStreamChunk,
    RequestOrigin,
    StreamAccumulator,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")
_STREAMING = ProviderCapabilities(supports_streaming=True)


def _gateway(provider: FakeModelProvider) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek", model="deepseek-chat", context_window=65536
            ),
        )
    )
    return DefaultLlmGateway(
        registry, resolver=DefaultModelSelectionResolver(catalog, current_model=_REF)
    )


def _request(cancel_token: CancelToken | None = None) -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
        cancel_token=cancel_token,
    )


def test_chunks_normalized_with_request_id_and_sequence() -> None:
    provider = FakeModelProvider(
        capabilities=_STREAMING,
        stream_chunks=(
            ProviderStreamChunk(delta_text="你"),
            ProviderStreamChunk(delta_text="好"),
            ProviderStreamChunk(
                usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
                finish_reason=FinishReason.STOP,
            ),
        ),
    )
    chunks = list(_gateway(provider).stream(_request()))
    assert [chunk.sequence for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.request_id == "req_1" for chunk in chunks)
    assert "".join(chunk.delta_text or "" for chunk in chunks) == "你好"


def test_final_chunk_carries_usage_and_finish_reason() -> None:
    provider = FakeModelProvider(
        capabilities=_STREAMING,
        stream_chunks=(
            ProviderStreamChunk(delta_text="answer"),
            ProviderStreamChunk(
                usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
                finish_reason=FinishReason.STOP,
            ),
        ),
    )
    chunks = list(_gateway(provider).stream(_request()))
    final = chunks[-1]
    assert final.usage_delta is not None
    assert final.finish_reason is FinishReason.STOP


def test_missing_provider_usage_estimated_in_synthetic_final_chunk() -> None:
    provider = FakeModelProvider(
        capabilities=_STREAMING,
        stream_chunks=(ProviderStreamChunk(delta_text="partial answer text"),),
    )
    chunks = list(_gateway(provider).stream(_request()))
    final = chunks[-1]
    assert final.usage_delta is not None
    assert final.usage_delta.estimated is True
    assert final.usage_delta.output_tokens > 0
    assert final.finish_reason is FinishReason.STOP


def test_cancel_mid_stream_yields_interrupted_tail() -> None:
    token = CancelToken()
    provider = FakeModelProvider(
        capabilities=_STREAMING,
        stream_chunks=(
            ProviderStreamChunk(delta_text="早期"),
            ProviderStreamChunk(delta_text="后续"),
            ProviderStreamChunk(finish_reason=FinishReason.STOP),
        ),
    )
    stream = _gateway(provider).stream(_request(cancel_token=token))
    first = next(stream)
    assert first.delta_text == "早期"
    token.cancel()
    rest = list(stream)
    final = rest[-1]
    assert final.interrupted is True
    assert final.finish_reason is FinishReason.USER_CANCELLED
    assert final.usage_delta is not None
    assert final.usage_delta.estimated is True


def test_stream_rejected_when_adapter_lacks_streaming() -> None:
    provider = FakeModelProvider()  # 默认 capabilities：不支持 streaming
    with pytest.raises(ModelBadRequestError):
        _gateway(provider).stream(_request())


def test_same_request_shape_works_for_complete_and_stream() -> None:
    provider = FakeModelProvider(
        content="ok",
        capabilities=_STREAMING,
        stream_chunks=(ProviderStreamChunk(delta_text="ok"),),
    )
    gw = _gateway(provider)
    request = _request()  # 同一结构、无 stream 标志（§3.3）
    assert gw.complete(request).content == "ok"
    assert list(gw.stream(request))


def test_accumulator_merges_stream_into_full_response_parts() -> None:
    provider = FakeModelProvider(
        capabilities=_STREAMING,
        stream_chunks=(
            ProviderStreamChunk(delta_text="hello "),
            ProviderStreamChunk(delta_text="world"),
            ProviderStreamChunk(
                usage=ModelUsage(input_tokens=1, output_tokens=2, total_tokens=3),
                finish_reason=FinishReason.STOP,
            ),
        ),
    )
    acc = StreamAccumulator()
    for chunk in _gateway(provider).stream(_request()):
        acc.add(chunk)
    assert acc.text == "hello world"
    assert acc.usage is not None
    assert acc.usage.total_tokens == 3
    assert acc.finish_reason is FinishReason.STOP
