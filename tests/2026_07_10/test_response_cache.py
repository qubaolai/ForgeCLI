"""响应缓存与 prompt cache 标注（ADR-0011 §14，2026-07-10 切片）。"""

from forgecli.application.llm.gateway import (
    CacheHint,
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    InMemoryResponseCache,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ProviderRegistry,
    RequestOrigin,
    StructuredModelRequest,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


def _gateway(
    provider: FakeModelProvider, cache: InMemoryResponseCache
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
        cache=cache,
    )


def _request(
    *,
    request_id: str = "req_1",
    origin: RequestOrigin = RequestOrigin.TITLE,
    text: str = "给会话起标题",
) -> ModelRequest:
    return ModelRequest(
        request_id=request_id,
        session_id="sess_1",
        turn_id="turn_0001",
        origin=origin,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock(text),)),),
        params=ModelParams(),
    )


def test_cache_hit_returns_cached_response_with_zero_usage() -> None:
    provider = FakeModelProvider(content="标题A")
    cache = InMemoryResponseCache(allowed_origins=(RequestOrigin.TITLE,))
    gw = _gateway(provider, cache)
    first = gw.complete(_request(request_id="req_1"))
    second = gw.complete(_request(request_id="req_2"))  # 指纹排除 request_id
    assert provider.complete_calls == 1
    assert first.cached is False
    assert second.cached is True
    assert second.content == "标题A"
    assert second.usage.total_tokens == 0  # 命中时真实 usage 记 0（§14）
    assert second.raw_metadata["cache_hit"] == "true"
    assert second.request_id == "req_2"


def test_chat_and_act_origins_never_cached_even_if_allowed() -> None:
    provider = FakeModelProvider(content="回复")
    cache = InMemoryResponseCache(
        allowed_origins=(RequestOrigin.CHAT, RequestOrigin.ACT, RequestOrigin.TITLE)
    )
    gw = _gateway(provider, cache)
    gw.complete(_request(origin=RequestOrigin.CHAT))
    gw.complete(_request(origin=RequestOrigin.CHAT))
    assert provider.complete_calls == 2  # chat 硬排除


def test_origin_not_in_allowlist_not_cached() -> None:
    provider = FakeModelProvider(content="x")
    cache = InMemoryResponseCache(allowed_origins=(RequestOrigin.TITLE,))
    gw = _gateway(provider, cache)
    gw.complete(_request(origin=RequestOrigin.SUMMARY))
    gw.complete(_request(origin=RequestOrigin.SUMMARY))
    assert provider.complete_calls == 2


def test_different_input_misses_cache() -> None:
    provider = FakeModelProvider(content="x")
    cache = InMemoryResponseCache(allowed_origins=(RequestOrigin.TITLE,))
    gw = _gateway(provider, cache)
    gw.complete(_request(text="输入一"))
    gw.complete(_request(text="输入二"))
    assert provider.complete_calls == 2


def test_default_cache_is_off() -> None:
    provider = FakeModelProvider(content="x")
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
        registry, resolver=DefaultModelSelectionResolver(catalog, current_model=_REF)
    )
    gw.complete(_request())
    gw.complete(_request())
    assert provider.complete_calls == 2  # 默认无响应缓存


def test_structured_degraded_result_cached_by_schema_aware_fingerprint() -> None:
    provider = FakeModelProvider(content='{"title": "t"}')
    cache = InMemoryResponseCache(
        allowed_origins=(RequestOrigin.STRUCTURED_CLASSIFICATION,)
    )
    gw = _gateway(provider, cache)
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }
    structured = StructuredModelRequest(
        model_request=_request(origin=RequestOrigin.STRUCTURED_CLASSIFICATION),
        schema=schema,
        schema_name="title_result",
    )
    first = gw.complete_structured(structured)
    second = gw.complete_structured(structured)
    assert provider.complete_calls == 1
    assert first.data == second.data == {"title": "t"}


def test_cache_hint_flows_to_request_without_error() -> None:
    provider = FakeModelProvider(content="ok")
    cache = InMemoryResponseCache()
    gw = _gateway(provider, cache)
    request = ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
        cache_hint=CacheHint(cache_system_prompt=True, cache_tools=True),
    )
    # 不支持 prompt 缓存的 provider 静默忽略标注，不报错（§14）。
    assert gw.complete(request).content == "ok"
