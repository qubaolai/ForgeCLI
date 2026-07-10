"""2026-06-30: ProviderRegistry adapter binding behavior."""

from __future__ import annotations

import pytest

from forgecli.application.llm.errors import UnknownProvider
from forgecli.application.llm.gateway.errors import ModelUnavailableError
from forgecli.application.llm.gateway.provider import (
    ModelProvider,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResponse,
)
from forgecli.application.llm.gateway.provider_registry import ProviderRegistry
from forgecli.application.llm.gateway.response import FinishReason
from forgecli.application.llm.providers import REGISTRY


class _StaticProvider(ModelProvider):
    def __init__(self, provider_id: str) -> None:
        self._provider_id = provider_id

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(content=request.model, finish_reason=FinishReason.STOP)


def test_static_provider_registry_contains_current_closed_provider_ids() -> None:
    assert {"deepseek", "mimo", "openai", "local"}.issubset(REGISTRY)


def test_provider_registry_registers_and_returns_known_adapter() -> None:
    provider = _StaticProvider("deepseek")
    registry = ProviderRegistry()

    registry.register(provider)

    assert registry.get("deepseek") is provider


def test_provider_registry_rejects_duplicate_adapter() -> None:
    registry = ProviderRegistry()
    registry.register(_StaticProvider("deepseek"))

    with pytest.raises(ValueError, match="重复注册"):
        registry.register(_StaticProvider("deepseek"))


def test_provider_registry_rejects_unknown_provider_on_register() -> None:
    registry = ProviderRegistry()

    with pytest.raises(UnknownProvider):
        registry.register(_StaticProvider("custom"))


def test_provider_registry_distinguishes_missing_adapter_from_unknown_provider() -> (
    None
):
    registry = ProviderRegistry()

    with pytest.raises(ModelUnavailableError) as missing:
        registry.get("deepseek")
    assert missing.value.provider == "deepseek"

    with pytest.raises(UnknownProvider):
        registry.get("custom")
