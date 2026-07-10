"""2026-06-30: DefaultLlmGateway complete happy path."""

from __future__ import annotations

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    DefaultLlmGateway,
    ExplicitModelSelection,
    FinishReason,
    ModelBadRequestError,
    ModelParams,
    ModelProviderInternalError,
    ModelRequest,
    ModelTimeoutError,
    ModelUsage,
    ProviderRegistry,
    TextBlock,
    TierModelSelection,
)
from forgecli.application.llm.gateway.origin import RequestOrigin
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters.fake_provider import FakeModelProvider


def _timer(values: tuple[float, float]):
    iterator = iter(values)
    return lambda: next(iterator)


def _request(
    *,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
) -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="ses_1",
        turn_id="turn_1",
        origin=RequestOrigin.CHAT,
        model_selection=ExplicitModelSelection(provider=provider, model=model),
        messages=(
            ChatMessage(
                role=MessageRole.USER,
                content=(TextBlock("hello"),),
            ),
        ),
        params=ModelParams(temperature=0.2),
        system_prompt="be concise",
        metadata={"mode": "chat"},
    )


def _gateway(provider: FakeModelProvider) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    return DefaultLlmGateway(registry, timer=_timer((10.0, 10.125)))


def test_complete_returns_normalized_response_with_provider_usage() -> None:
    usage = ModelUsage(input_tokens=5, output_tokens=2, total_tokens=7)
    gateway = _gateway(
        FakeModelProvider(
            provider_id="deepseek",
            content="fixed reply",
            usage=usage,
            raw_metadata={"request_id": "provider-req-1"},
        )
    )

    response = gateway.complete(_request())

    assert response.request_id == "req_1"
    assert response.provider == "deepseek"
    assert response.model == "deepseek-chat"
    assert response.content == "fixed reply"
    assert response.finish_reason is FinishReason.STOP
    assert response.usage == usage
    assert response.usage.estimated is False
    assert response.latency_ms == 125.0
    assert response.raw_metadata == {"request_id": "provider-req-1"}


def test_complete_estimates_usage_when_provider_omits_usage() -> None:
    gateway = _gateway(FakeModelProvider(provider_id="deepseek", content="estimated"))

    response = gateway.complete(_request())

    assert response.content == "estimated"
    assert response.usage == ModelUsage(
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        estimated=True,
    )


def test_complete_preserves_gateway_errors_from_provider() -> None:
    error = ModelTimeoutError("timeout", provider="deepseek", model="deepseek-chat")
    gateway = _gateway(FakeModelProvider(provider_id="deepseek", error=error))

    with pytest.raises(ModelTimeoutError) as exc_info:
        gateway.complete(_request())

    assert exc_info.value is error


def test_complete_wraps_unexpected_provider_errors() -> None:
    gateway = _gateway(
        FakeModelProvider(provider_id="deepseek", error=RuntimeError("boom"))
    )

    with pytest.raises(ModelProviderInternalError) as exc_info:
        gateway.complete(_request())

    assert exc_info.value.provider == "deepseek"
    assert exc_info.value.model == "deepseek-chat"
    assert exc_info.value.request_id == "req_1"


def test_complete_rejects_non_explicit_selection_in_mvp() -> None:
    gateway = _gateway(FakeModelProvider(provider_id="deepseek"))
    request = _request()
    request = ModelRequest(
        request_id=request.request_id,
        session_id=request.session_id,
        turn_id=request.turn_id,
        origin=request.origin,
        model_selection=TierModelSelection("fast"),
        messages=request.messages,
        params=request.params,
    )

    with pytest.raises(ModelBadRequestError, match="explicit selection"):
        gateway.complete(request)
