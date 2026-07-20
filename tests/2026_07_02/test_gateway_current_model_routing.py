"""DefaultLlmGateway 接线 resolver 后：current_model 路由端到端跑通（0702）。

（2026-07-02）全部离线：内存 catalog + resolver + FakeModelProvider 装配。
证明注入 resolver 后 current_model 不再被拒，且按当前模型 / 用途覆盖路由到对应 adapter；
resolver 的校验错误如实上抛、不被 fallback 掩盖。
"""

from __future__ import annotations

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    ExplicitModelSelection,
    InMemoryModelCatalog,
    ModelBadRequestError,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ModelResponse,
    ModelSelection,
    ProviderRegistry,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_DEEPSEEK = ModelRef("deepseek", "deepseek-chat")
_OPENAI = ModelRef("openai", "gpt-4o")


def _entry(ref: ModelRef, *, context_window: int = 64000) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        provider=ref.provider, model=ref.model, context_window=context_window
    )


def _request(
    selection: ModelSelection, origin: RequestOrigin = RequestOrigin.CHAT
) -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=origin,
        model_selection=selection,
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )


def _gateway(
    resolver: DefaultModelSelectionResolver,
    *providers: FakeModelProvider,
) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    for provider in providers or (FakeModelProvider(content="ok"),):
        registry.register(provider)
    return DefaultLlmGateway(registry, resolver=resolver)


def _resolver(
    *entries: ModelCatalogEntry,
    current_model: ModelRef | None = _DEEPSEEK,
    overrides: dict[RequestOrigin, ModelRef] | None = None,
) -> DefaultModelSelectionResolver:
    catalog = InMemoryModelCatalog(entries or (_entry(_DEEPSEEK),))
    return DefaultModelSelectionResolver(
        catalog, current_model=current_model, overrides=overrides or {}
    )


def test_current_model_routes_through_resolver() -> None:
    gw = _gateway(
        _resolver(_entry(_DEEPSEEK)),
        FakeModelProvider(provider_id="deepseek", content="from current"),
    )
    resp = gw.complete(_request(CurrentModelSelection()))
    assert isinstance(resp, ModelResponse)
    assert resp.provider == "deepseek"
    assert resp.model == "deepseek-chat"
    assert resp.content == "from current"


def test_purpose_override_routes_to_override_provider() -> None:
    resolver = _resolver(
        _entry(_DEEPSEEK),
        _entry(_OPENAI, context_window=128000),
        overrides={RequestOrigin.PLAN: _OPENAI},
    )
    gw = _gateway(
        resolver,
        FakeModelProvider(provider_id="deepseek", content="current"),
        FakeModelProvider(provider_id="openai", content="override"),
    )
    resp = gw.complete(_request(CurrentModelSelection(), origin=RequestOrigin.PLAN))
    assert resp.provider == "openai"
    assert resp.model == "gpt-4o"
    assert resp.content == "override"


def test_explicit_selection_still_routes() -> None:
    gw = _gateway(
        _resolver(_entry(_DEEPSEEK)),
        FakeModelProvider(provider_id="deepseek", content="explicit"),
    )
    resp = gw.complete(_request(ExplicitModelSelection("deepseek", "deepseek-chat")))
    assert resp.content == "explicit"


def test_resolver_validation_error_propagates() -> None:
    # 目录里没有该模型：resolver 抛 ModelBadRequestError，网关如实上抛、不 fallback。
    gw = _gateway(_resolver(_entry(_DEEPSEEK)))
    with pytest.raises(ModelBadRequestError):
        gw.complete(_request(ExplicitModelSelection("deepseek", "ghost")))
