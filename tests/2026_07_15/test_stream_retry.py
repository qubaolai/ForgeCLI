"""stream 首包前重试环与首包后中断收尾（ADR-0012 §2，2026-07-15 切片）。

覆盖：首包前 auth 换凭证重试 / 429 短等重试 / timeout 同凭证重试、
首包后错误只产出中断收尾（不重试、不重放）、空流收尾、重试耗尽上抛。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    FinishReason,
    InMemoryModelCatalog,
    ModelAuthError,
    ModelCatalogEntry,
    ModelParams,
    ModelProviderInternalError,
    ModelRateLimitError,
    ModelRequest,
    ModelTimeoutError,
    ModelUsage,
    ProviderCapabilities,
    ProviderRegistry,
    ProviderRuntimeSettings,
    ProviderSettingsSource,
    ProviderStreamChunk,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider
from forgecli.infrastructure.llm.credentials import (
    EnvCredentialResolver,
    InMemoryCredentialPool,
)

_REF = ModelRef(provider="deepseek", model="deepseek-chat")
_STREAMING = ProviderCapabilities(supports_streaming=True)

# 脚本项：Exception 表示在该位置抛错，否则为 ProviderStreamChunk。
_Script = list[object]


class _ScriptedStreamProvider(FakeModelProvider):
    """每次 stream 调用消费一个脚本：按序产出 chunk 或抛错。"""

    def __init__(self, scripts: list[_Script]) -> None:
        super().__init__(capabilities=_STREAMING)
        self._scripts = scripts
        self.stream_calls = 0

    def stream(self, request: object) -> Iterator[ProviderStreamChunk]:
        self.last_request = request  # type: ignore[assignment]
        self.stream_calls += 1
        script = self._scripts.pop(0)
        return self._play(script)

    @staticmethod
    def _play(script: _Script) -> Iterator[ProviderStreamChunk]:
        for item in script:
            if isinstance(item, Exception):
                raise item
            assert isinstance(item, ProviderStreamChunk)
            yield item


class _StubSettings(ProviderSettingsSource):
    def __init__(self, refs: tuple[str, ...], *, max_retries: int = 2) -> None:
        self._refs = refs
        self._max_retries = max_retries

    def settings_for(self, provider_id: str) -> ProviderRuntimeSettings:
        return ProviderRuntimeSettings(
            provider_id=provider_id,
            timeout_seconds=60.0,
            max_retries=self._max_retries,
            credential_refs=self._refs,
            wait_threshold_seconds=5.0,
        )


def _gateway(
    provider: FakeModelProvider,
    *,
    env: dict[str, str] | None = None,
    refs: tuple[str, ...] = (),
    sleeps: list[float] | None = None,
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
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        settings_source=_StubSettings(refs, max_retries=max_retries),
        credential_pool=InMemoryCredentialPool(
            EnvCredentialResolver(getenv=(env or {}).get)
        ),
        sleeper=(sleeps if sleeps is not None else []).append,
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


def _final(text: str) -> _Script:
    return [
        ProviderStreamChunk(delta_text=text),
        ProviderStreamChunk(
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            finish_reason=FinishReason.STOP,
        ),
    ]


def test_auth_failure_before_first_chunk_switches_credential() -> None:
    provider = _ScriptedStreamProvider([[ModelAuthError("401")], _final("ok")])
    gw = _gateway(
        provider,
        env={"KEY_A": "bad", "KEY_B": "good"},
        refs=("KEY_A", "KEY_B"),
    )
    chunks = list(gw.stream(_request()))
    assert provider.stream_calls == 2  # 首包前允许换凭证重试
    assert "".join(chunk.delta_text or "" for chunk in chunks) == "ok"
    assert chunks[-1].finish_reason is FinishReason.STOP
    request = provider.last_request
    assert request is not None and request.credential is not None
    assert request.credential.ref == "KEY_B"


def test_429_small_retry_after_waits_before_first_chunk() -> None:
    sleeps: list[float] = []
    provider = _ScriptedStreamProvider(
        [[ModelRateLimitError("429", retry_after=2.0)], _final("ok")]
    )
    gw = _gateway(provider, env={"KEY_A": "a"}, refs=("KEY_A",), sleeps=sleeps)
    chunks = list(gw.stream(_request()))
    assert sleeps == [2.0]
    assert provider.stream_calls == 2
    request = provider.last_request
    assert request is not None and request.credential is not None
    assert request.credential.ref == "KEY_A"  # 同一凭证
    assert chunks[-1].finish_reason is FinishReason.STOP


def test_timeout_before_first_chunk_retries_same_credential() -> None:
    provider = _ScriptedStreamProvider([[ModelTimeoutError("t")], _final("ok")])
    gw = _gateway(provider, env={"KEY_A": "a"}, refs=("KEY_A",))
    chunks = list(gw.stream(_request()))
    assert provider.stream_calls == 2
    assert chunks[-1].finish_reason is FinishReason.STOP


def test_error_after_first_chunk_interrupts_without_retry() -> None:
    provider = _ScriptedStreamProvider(
        [
            [
                ProviderStreamChunk(delta_text="部分内容"),
                ModelProviderInternalError("boom"),
            ]
        ]
    )
    gw = _gateway(provider)
    chunks = list(gw.stream(_request()))
    assert provider.stream_calls == 1  # 首包之后不重试、不重放
    assert chunks[0].delta_text == "部分内容"
    tail = chunks[-1]
    assert tail.interrupted is True
    assert tail.finish_reason is FinishReason.ERROR
    assert tail.usage_delta is not None
    assert tail.usage_delta.estimated is True


def test_empty_stream_still_gets_final_summary_chunk() -> None:
    provider = _ScriptedStreamProvider([[]])
    gw = _gateway(provider)
    chunks = list(gw.stream(_request()))
    assert len(chunks) == 1
    assert chunks[0].finish_reason is FinishReason.STOP
    assert chunks[0].usage_delta is not None
    assert chunks[0].usage_delta.estimated is True


def test_first_chunk_retries_exhausted_raise_last_error() -> None:
    provider = _ScriptedStreamProvider(
        [[ModelAuthError("401")], [ModelAuthError("401")]]
    )
    gw = _gateway(
        provider,
        env={"KEY_A": "a", "KEY_B": "b"},
        refs=("KEY_A", "KEY_B"),
        max_retries=1,
    )
    with pytest.raises(ModelAuthError):
        list(gw.stream(_request()))
    assert provider.stream_calls == 2
