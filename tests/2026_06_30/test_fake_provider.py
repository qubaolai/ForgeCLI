"""FakeModelProvider：成功 / 无 usage / provider error 三态 + 不读环境。"""

from __future__ import annotations

import pytest

from forgecli.application.llm.gateway import (
    FinishReason,
    ModelParams,
    ModelRateLimitError,
    ModelUsage,
    ProviderCapabilities,
    ProviderRequest,
)
from forgecli.infrastructure.llm.adapters import FakeModelProvider


def _provider_request() -> ProviderRequest:
    return ProviderRequest(model="deepseek-chat", messages=(), params=ModelParams())


def test_success_returns_content_and_injected_usage() -> None:
    usage = ModelUsage(input_tokens=3, output_tokens=5, total_tokens=8)
    provider = FakeModelProvider(content="hello", usage=usage)
    response = provider.complete(_provider_request())
    assert response.content == "hello"
    assert response.usage is usage
    assert response.finish_reason is FinishReason.STOP


def test_no_usage_returns_none() -> None:
    response = FakeModelProvider(content="hi").complete(_provider_request())
    assert response.usage is None


def test_injected_error_is_raised() -> None:
    provider = FakeModelProvider(error=ModelRateLimitError("429"))
    with pytest.raises(ModelRateLimitError):
        provider.complete(_provider_request())


def test_capabilities_returns_injected() -> None:
    caps = ProviderCapabilities(supports_tools=True)
    assert FakeModelProvider(capabilities=caps).capabilities() is caps


def test_does_not_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # 清掉可能的凭证环境变量，fake 仍可构造并返回确定结果，证明不读 env / 配置。
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = FakeModelProvider(provider_id="deepseek", content="ok")
    assert provider.complete(_provider_request()).content == "ok"
    assert provider.provider_id == "deepseek"
