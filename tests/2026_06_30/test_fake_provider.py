"""2026-06-30: FakeModelProvider deterministic adapter behavior."""

from __future__ import annotations

import pytest

from forgecli.application.llm.gateway.errors import ModelTimeoutError
from forgecli.application.llm.gateway.provider import (
    ProviderCapabilities,
    ProviderRequest,
)
from forgecli.application.llm.gateway.response import FinishReason, ModelUsage
from forgecli.infrastructure.llm.adapters.fake_provider import FakeModelProvider


def _provider_request() -> ProviderRequest:
    from forgecli.application.llm.gateway.params import ModelParams

    return ProviderRequest(model="deepseek-chat", messages=(), params=ModelParams())


def test_fake_provider_exposes_configured_provider_id() -> None:
    assert FakeModelProvider(provider_id="deepseek").provider_id == "deepseek"


def test_fake_provider_returns_fixed_response_usage_and_metadata() -> None:
    usage = ModelUsage(input_tokens=7, output_tokens=3, total_tokens=10)
    provider = FakeModelProvider(
        content="fixed reply",
        usage=usage,
        finish_reason=FinishReason.LENGTH,
        capabilities=ProviderCapabilities(supports_structured_output=True),
        raw_metadata={"request_id": "provider-req-1"},
    )

    response = provider.complete(_provider_request())

    assert response.content == "fixed reply"
    assert response.finish_reason is FinishReason.LENGTH
    assert response.usage == usage
    assert response.raw_metadata == {"request_id": "provider-req-1"}
    assert provider.capabilities().supports_structured_output is True


def test_fake_provider_can_omit_usage_for_gateway_estimation() -> None:
    response = FakeModelProvider(content="no usage", usage=None).complete(
        _provider_request()
    )

    assert response.content == "no usage"
    assert response.usage is None


def test_fake_provider_raises_injected_error() -> None:
    error = ModelTimeoutError("timeout", provider="deepseek", model="deepseek-chat")
    provider = FakeModelProvider(error=error)

    with pytest.raises(ModelTimeoutError) as exc_info:
        provider.complete(_provider_request())

    assert exc_info.value is error
