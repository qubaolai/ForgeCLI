"""响应缓存完备化：TTL / LRU / native structured 纳入（ADR-0012 §3）。"""

from __future__ import annotations

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    FinishReason,
    InMemoryModelCatalog,
    InMemoryResponseCache,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderRegistry,
    RequestOrigin,
    StructuredModelRequest,
    TextBlock,
    ThinkingConfig,
    ThinkingMode,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _request(text: str, *, request_id: str = "req_1") -> ModelRequest:
    return ModelRequest(
        request_id=request_id,
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.TITLE,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock(text),)),),
        params=ModelParams(),
    )


def _response(content: str) -> ModelResponse:
    return ModelResponse(
        request_id="req_src",
        provider="deepseek",
        model="deepseek-chat",
        content=content,
        finish_reason=FinishReason.STOP,
        usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        latency_ms=10.0,
    )


def test_ttl_lazy_expiry_with_injected_clock() -> None:
    clock = _Clock()
    cache = InMemoryResponseCache(
        allowed_origins=(RequestOrigin.TITLE,), ttl_seconds=10.0, clock=clock
    )
    cache.store(_request("q"), _REF, _response("a"))
    clock.now = 9.0
    assert cache.lookup(_request("q"), _REF) is not None
    clock.now = 11.0
    assert cache.lookup(_request("q"), _REF) is None  # 惰性过期
    # 过期后重新写入可再次命中。
    cache.store(_request("q"), _REF, _response("a2"))
    clock.now = 12.0
    hit = cache.lookup(_request("q"), _REF)
    assert hit is not None and hit.content == "a2"


def test_lru_eviction_at_capacity() -> None:
    cache = InMemoryResponseCache(allowed_origins=(RequestOrigin.TITLE,), max_entries=2)
    cache.store(_request("q1"), _REF, _response("a1"))
    cache.store(_request("q2"), _REF, _response("a2"))
    # 命中 q1，刷新其 LRU 顺位；随后写入 q3 应淘汰最久未命中的 q2。
    assert cache.lookup(_request("q1"), _REF) is not None
    cache.store(_request("q3"), _REF, _response("a3"))
    assert cache.lookup(_request("q2"), _REF) is None
    assert cache.lookup(_request("q1"), _REF) is not None
    assert cache.lookup(_request("q3"), _REF) is not None


def test_no_ttl_means_no_expiry() -> None:
    clock = _Clock()
    cache = InMemoryResponseCache(
        allowed_origins=(RequestOrigin.TITLE,), ttl_seconds=None, clock=clock
    )
    cache.store(_request("q"), _REF, _response("a"))
    clock.now = 1_000_000.0
    assert cache.lookup(_request("q"), _REF) is not None


def test_model_thinking_is_part_of_cache_fingerprint() -> None:
    cache = InMemoryResponseCache(allowed_origins=(RequestOrigin.TITLE,))
    off = ThinkingConfig(enabled=ThinkingMode.OFF)
    on = ThinkingConfig(enabled=ThinkingMode.ON)
    cache.store(_request("q"), _REF, _response("off"), thinking=off)

    assert cache.lookup(_request("q"), _REF, thinking=off) is not None
    assert cache.lookup(_request("q"), _REF, thinking=on) is None


def _structured_gateway(
    provider: FakeModelProvider, cache: InMemoryResponseCache
) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek",
                model="deepseek-chat",
                context_window=65536,
                supports_structured_output=True,  # 走 native 通道
            ),
        )
    )
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        cache=cache,
    )


def _structured(schema_name: str, *, required: str = "title") -> StructuredModelRequest:
    return StructuredModelRequest(
        model_request=ModelRequest(
            request_id="req_1",
            session_id="sess_1",
            turn_id="turn_0001",
            origin=RequestOrigin.STRUCTURED_CLASSIFICATION,
            model_selection=CurrentModelSelection(),
            messages=(
                ChatMessage(role=MessageRole.USER, content=(TextBlock("分类"),)),
            ),
            params=ModelParams(),
        ),
        schema={
            "type": "object",
            "properties": {required: {"type": "string"}},
            "required": [required],
        },
        schema_name=schema_name,
        strict=False,
    )


def test_native_structured_output_cached() -> None:
    provider = FakeModelProvider(content='{"title": "t"}')
    cache = InMemoryResponseCache(
        allowed_origins=(RequestOrigin.STRUCTURED_CLASSIFICATION,)
    )
    gw = _structured_gateway(provider, cache)
    first = gw.complete_structured(_structured("title_result"))
    second = gw.complete_structured(_structured("title_result"))
    assert provider.complete_calls == 1  # 第二次命中缓存
    assert first.data == second.data == {"title": "t"}


def test_native_structured_different_schema_not_mixed() -> None:
    provider = FakeModelProvider(content='{"title": "t"}')
    cache = InMemoryResponseCache(
        allowed_origins=(RequestOrigin.STRUCTURED_CLASSIFICATION,)
    )
    gw = _structured_gateway(provider, cache)
    gw.complete_structured(_structured("schema_a"))
    gw.complete_structured(_structured("schema_b"))
    assert provider.complete_calls == 2  # schema 摘要并入指纹，不串味


def test_chat_origin_never_cached_even_with_ttl_config() -> None:
    provider = FakeModelProvider(content="回复")
    cache = InMemoryResponseCache(
        allowed_origins=(RequestOrigin.CHAT,), ttl_seconds=600.0
    )
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek", model="deepseek-chat", context_window=65536
            ),
        )
    )
    gw = DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        cache=cache,
    )
    chat = ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )
    gw.complete(chat)
    gw.complete(chat)
    assert provider.complete_calls == 2  # chat 硬排除不变
