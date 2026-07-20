"""429 短等重试与重试环（ADR-0012 §2，2026-07-15 切片）。

覆盖：retry_after <= 阈值同凭证等待重试（注入 sleeper，不真实 sleep）、
超阈值 / 无 retry_after 换凭证、keyless 短等、wait 计入 max_retries 预算、
wait_retries 写入安全摘要。全部离线确定性。
"""

from __future__ import annotations

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelCatalogEntry,
    ModelParams,
    ModelRateLimitError,
    ModelRequest,
    ProviderRegistry,
    ProviderRuntimeSettings,
    ProviderSettingsSource,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.gateway.provider import (
    ProviderRequest,
    ProviderResponse,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider
from forgecli.infrastructure.llm.credentials import (
    EnvCredentialResolver,
    InMemoryCredentialPool,
)


class _StubSettings(ProviderSettingsSource):
    def __init__(
        self,
        refs: tuple[str, ...],
        *,
        max_retries: int = 2,
        wait_threshold: float = 5.0,
    ) -> None:
        self._refs = refs
        self._max_retries = max_retries
        self._wait_threshold = wait_threshold

    def settings_for(self, provider_id: str) -> ProviderRuntimeSettings:
        return ProviderRuntimeSettings(
            provider_id=provider_id,
            timeout_seconds=60.0,
            max_retries=self._max_retries,
            credential_refs=self._refs,
            wait_threshold_seconds=self._wait_threshold,
        )


class _FlakyProvider(FakeModelProvider):
    """按脚本先抛错再成功，模拟 429 恢复。"""

    def __init__(
        self, errors: list[Exception], *, provider_id: str = "deepseek"
    ) -> None:
        super().__init__(provider_id=provider_id, content="recovered")
        self._errors = errors

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.last_request = request
        self.complete_calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return ProviderResponse(
            content=self._content, finish_reason=self._finish_reason
        )


def _gateway(
    provider: FakeModelProvider,
    env: dict[str, str],
    refs: tuple[str, ...],
    sleeps: list[float],
    *,
    provider_id: str = "deepseek",
    model: str = "deepseek-chat",
    max_retries: int = 2,
    wait_threshold: float = 5.0,
) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (ModelCatalogEntry(provider=provider_id, model=model, context_window=65536),)
    )
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(
            catalog, current_model=ModelRef(provider=provider_id, model=model)
        ),
        settings_source=_StubSettings(
            refs, max_retries=max_retries, wait_threshold=wait_threshold
        ),
        credential_pool=InMemoryCredentialPool(EnvCredentialResolver(getenv=env.get)),
        sleeper=sleeps.append,
    )


def _request() -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )


def test_small_retry_after_waits_and_retries_same_credential() -> None:
    provider = _FlakyProvider([ModelRateLimitError("429", retry_after=2.0)])
    sleeps: list[float] = []
    gw = _gateway(provider, {"KEY_A": "a", "KEY_B": "b"}, ("KEY_A", "KEY_B"), sleeps)
    resp = gw.complete(_request())
    assert resp.content == "recovered"
    assert provider.complete_calls == 2
    assert sleeps == [2.0]  # 经注入 sleeper 等待，不真实 sleep
    assert provider.last_request is not None
    assert provider.last_request.credential is not None
    assert provider.last_request.credential.ref == "KEY_A"  # 同一凭证
    assert resp.raw_metadata["wait_retries"] == "1"
    assert "credential_retries" not in resp.raw_metadata


def test_retry_after_above_threshold_switches_credential() -> None:
    provider = _FlakyProvider([ModelRateLimitError("429", retry_after=30.0)])
    sleeps: list[float] = []
    gw = _gateway(provider, {"KEY_A": "a", "KEY_B": "b"}, ("KEY_A", "KEY_B"), sleeps)
    resp = gw.complete(_request())
    assert resp.content == "recovered"
    assert sleeps == []  # 不等待，直接冷却换凭证
    assert provider.last_request is not None
    assert provider.last_request.credential is not None
    assert provider.last_request.credential.ref == "KEY_B"
    assert resp.raw_metadata["credential_retries"] == "1"
    assert "wait_retries" not in resp.raw_metadata


def test_429_without_retry_after_switches_credential() -> None:
    provider = _FlakyProvider([ModelRateLimitError("429")])
    sleeps: list[float] = []
    gw = _gateway(provider, {"KEY_A": "a", "KEY_B": "b"}, ("KEY_A", "KEY_B"), sleeps)
    gw.complete(_request())
    assert sleeps == []
    assert provider.last_request is not None
    assert provider.last_request.credential is not None
    assert provider.last_request.credential.ref == "KEY_B"


def test_keyless_provider_can_wait_retry_on_429() -> None:
    provider = _FlakyProvider(
        [ModelRateLimitError("429", retry_after=1.5)], provider_id="local"
    )
    sleeps: list[float] = []
    gw = _gateway(provider, {}, (), sleeps, provider_id="local", model="llama")
    resp = gw.complete(_request())
    assert resp.content == "recovered"
    assert provider.complete_calls == 2
    assert sleeps == [1.5]


def test_wait_retries_count_into_max_retries_budget() -> None:
    provider = FakeModelProvider(error=ModelRateLimitError("429", retry_after=1.0))
    sleeps: list[float] = []
    gw = _gateway(provider, {"KEY_A": "a"}, ("KEY_A",), sleeps, max_retries=1)
    with pytest.raises(ModelRateLimitError):
        gw.complete(_request())
    assert provider.complete_calls == 2  # max_retries=1 -> 最多 2 次尝试
