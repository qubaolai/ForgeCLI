"""gateway 凭证级重试环（ADR-0011 §7 / §12，2026-07-08 切片）。

覆盖：auth/429 依次换 credential、重试上限、未配置凭证不发起调用、keyless 直通、
timeout 同凭证有限重试、重试次数写入安全摘要、凭证注入 ProviderRequest。
"""

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelAuthError,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ModelTimeoutError,
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

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


class _StubSettings(ProviderSettingsSource):
    def __init__(self, refs: tuple[str, ...], max_retries: int = 2) -> None:
        self._refs = refs
        self._max_retries = max_retries

    def settings_for(self, provider_id: str) -> ProviderRuntimeSettings:
        return ProviderRuntimeSettings(
            provider_id=provider_id,
            timeout_seconds=60.0,
            max_retries=self._max_retries,
            credential_refs=self._refs,
        )


class _FlakyProvider(FakeModelProvider):
    """按脚本先抛错再成功，模拟凭证/网络恢复。"""

    def __init__(self, errors: list[Exception]) -> None:
        super().__init__(content="recovered")
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
    *,
    max_retries: int = 2,
) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek", model="deepseek-chat", context_window=65536
            ),
        )
    )
    pool = InMemoryCredentialPool(EnvCredentialResolver(getenv=env.get))
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        settings_source=_StubSettings(refs, max_retries=max_retries),
        credential_pool=pool,
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


def test_credential_injected_into_provider_request() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, {"KEY_A": "fake-value"}, ("KEY_A",))
    gw.complete(_request())
    assert provider.last_request is not None
    credential = provider.last_request.credential
    assert credential is not None
    assert credential.ref == "KEY_A"
    assert credential.value == "fake-value"


def test_auth_failure_switches_to_next_credential() -> None:
    provider = _FlakyProvider([ModelAuthError("401")])
    gw = _gateway(provider, {"KEY_A": "bad", "KEY_B": "good"}, ("KEY_A", "KEY_B"))
    resp = gw.complete(_request())
    assert resp.content == "recovered"
    assert provider.complete_calls == 2
    # 第二次调用使用了下一个 credential，且重试次数进入安全摘要。
    assert provider.last_request is not None
    assert provider.last_request.credential is not None
    assert provider.last_request.credential.ref == "KEY_B"
    assert resp.raw_metadata["credential_retries"] == "1"


def test_retries_bounded_by_max_retries() -> None:
    provider = FakeModelProvider(error=ModelAuthError("401"))
    gw = _gateway(
        provider,
        {"KEY_A": "a", "KEY_B": "b", "KEY_C": "c"},
        ("KEY_A", "KEY_B", "KEY_C"),
        max_retries=1,
    )
    with pytest.raises(ModelAuthError):
        gw.complete(_request())
    assert provider.complete_calls == 2  # max_retries=1 -> 最多 2 次尝试


def test_unconfigured_credential_never_reaches_provider() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, {}, ("MISSING_KEY",))
    with pytest.raises(ModelAuthError) as excinfo:
        gw.complete(_request())
    assert provider.complete_calls == 0  # 不发起任何（网络）请求
    assert excinfo.value.provider == "deepseek"


def test_keyless_provider_called_without_credential() -> None:
    provider = FakeModelProvider(content="ok", provider_id="local")
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (ModelCatalogEntry(provider="local", model="llama", context_window=8192),)
    )
    gw = DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(
            catalog, current_model=ModelRef(provider="local", model="llama")
        ),
        settings_source=_StubSettings(()),
        credential_pool=InMemoryCredentialPool(EnvCredentialResolver(getenv={}.get)),
    )
    assert gw.complete(_request()).content == "ok"
    assert provider.last_request is not None
    assert provider.last_request.credential is None


def test_timeout_retried_with_same_credential() -> None:
    provider = _FlakyProvider([ModelTimeoutError("timeout")])
    gw = _gateway(provider, {"KEY_A": "value"}, ("KEY_A",))
    resp = gw.complete(_request())
    assert resp.content == "recovered"
    assert provider.complete_calls == 2
    assert provider.last_request is not None
    assert provider.last_request.credential is not None
    assert provider.last_request.credential.ref == "KEY_A"  # 未换凭证
    assert resp.raw_metadata["transport_retries"] == "1"


def test_timeout_retries_exhausted_raise_timeout() -> None:
    provider = FakeModelProvider(error=ModelTimeoutError("timeout"))
    gw = _gateway(provider, {"KEY_A": "value"}, ("KEY_A",), max_retries=2)
    with pytest.raises(ModelTimeoutError):
        gw.complete(_request())
    assert provider.complete_calls == 3


def test_retry_never_switches_provider_or_model() -> None:
    provider = _FlakyProvider([ModelAuthError("401")])
    gw = _gateway(provider, {"KEY_A": "a", "KEY_B": "b"}, ("KEY_A", "KEY_B"))
    resp = gw.complete(_request())
    assert resp.provider == "deepseek"
    assert resp.model == "deepseek-chat"
    assert provider.last_request is not None
    assert provider.last_request.model == "deepseek-chat"
