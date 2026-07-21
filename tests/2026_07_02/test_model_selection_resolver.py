"""DefaultModelSelectionResolver：当前模型 / 用途覆盖 / catalog 能力校验（0702）。

（2026-07-02）全部离线：catalog 用内存 InMemoryModelCatalog，覆盖表直接注入。
覆盖计划六项：默认解析、用途覆盖、未知 provider、未知 model、allowlist 拒绝、能力不足，
外加 min_context_window 超窗、未配置当前模型、explicit 绕过覆盖等边界。
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from forgecli.application.llm.gateway import (
    CurrentModelSelection,
    DefaultModelSelectionResolver,
    ExplicitModelSelection,
    InMemoryModelCatalog,
    ModelBadRequestError,
    ModelCatalogEntry,
    ModelContextOverflowError,
    RequestOrigin,
    ResolvedModel,
)
from forgecli.application.llm.model_ref import ModelRef

_CURRENT = ModelRef("deepseek", "deepseek-chat")
_OVERRIDE = ModelRef("openai", "gpt-4o")


def _entry(
    *,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    context_window: int = 64000,
    supports_structured_output: bool = False,
    supports_tool_calling: bool = False,
    allowlisted: bool = True,
    deprecated: bool = False,
) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        provider=provider,
        model=model,
        context_window=context_window,
        supports_structured_output=supports_structured_output,
        supports_tool_calling=supports_tool_calling,
        allowlisted=allowlisted,
        deprecated=deprecated,
    )


def _resolver(
    *entries: ModelCatalogEntry,
    current_model: ModelRef | None = _CURRENT,
    overrides: Mapping[RequestOrigin, ModelRef] | None = None,
) -> DefaultModelSelectionResolver:
    catalog = InMemoryModelCatalog(entries or (_entry(),))
    return DefaultModelSelectionResolver(
        catalog, current_model=current_model, overrides=overrides or {}
    )


def test_default_resolution_uses_current_model_for_all_origins() -> None:
    entry = _entry()
    resolver = _resolver(entry)
    for origin in (RequestOrigin.CHAT, RequestOrigin.PLAN, RequestOrigin.TITLE):
        out = resolver.resolve(CurrentModelSelection(), origin=origin)
        assert isinstance(out, ResolvedModel)
        assert out.ref == _CURRENT
        assert out.entry is entry


def test_purpose_override_applies_only_to_configured_origin() -> None:
    resolver = _resolver(
        _entry(),
        _entry(provider="openai", model="gpt-4o", context_window=128000),
        overrides={RequestOrigin.PLAN: _OVERRIDE},
    )
    # 命中覆盖的用途走覆盖模型。
    planned = resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.PLAN)
    assert planned.ref == _OVERRIDE
    # 未被覆盖的用途仍走当前主模型（不生成第二默认模型）。
    chatted = resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.CHAT)
    assert chatted.ref == _CURRENT


def test_unknown_provider_raises_bad_request() -> None:
    resolver = _resolver(_entry())  # 目录只含 deepseek:deepseek-chat
    with pytest.raises(ModelBadRequestError):
        resolver.resolve(
            ExplicitModelSelection("no-such-provider", "x"),
            origin=RequestOrigin.CHAT,
        )


def test_unknown_model_raises_bad_request() -> None:
    resolver = _resolver(_entry())
    with pytest.raises(ModelBadRequestError):
        resolver.resolve(
            ExplicitModelSelection("deepseek", "does-not-exist"),
            origin=RequestOrigin.CHAT,
        )


def test_non_allowlisted_model_rejected() -> None:
    resolver = _resolver(_entry(allowlisted=False))
    with pytest.raises(ModelBadRequestError, match="allowlist"):
        resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.CHAT)


def test_capability_shortfall_rejected() -> None:
    resolver = _resolver(_entry(supports_tool_calling=False))
    with pytest.raises(ModelBadRequestError, match="tool_calling"):
        resolver.resolve(
            CurrentModelSelection(),
            origin=RequestOrigin.ACT,
            required_capabilities=("tool_calling",),
        )


def test_capability_satisfied_passes() -> None:
    entry = _entry(supports_tool_calling=True, supports_structured_output=True)
    resolver = _resolver(entry)
    out = resolver.resolve(
        CurrentModelSelection(),
        origin=RequestOrigin.ACT,
        required_capabilities=("tool_calling", "structured_output"),
    )
    assert out.entry is entry


def test_unknown_capability_string_rejected() -> None:
    resolver = _resolver(_entry())
    with pytest.raises(ModelBadRequestError, match="未知能力诉求"):
        resolver.resolve(
            CurrentModelSelection(),
            origin=RequestOrigin.CHAT,
            required_capabilities=("telepathy",),
        )


def test_min_context_window_shortfall_raises_context_overflow() -> None:
    resolver = _resolver(_entry(context_window=128000))
    with pytest.raises(ModelContextOverflowError):
        resolver.resolve(
            CurrentModelSelection(),
            origin=RequestOrigin.CHAT,
            min_context_window=200000,
        )


def test_min_context_window_within_window_passes() -> None:
    resolver = _resolver(_entry(context_window=128000))
    out = resolver.resolve(
        CurrentModelSelection(),
        origin=RequestOrigin.CHAT,
        min_context_window=128000,
    )
    assert out.ref == _CURRENT


def test_current_model_none_without_override_raises() -> None:
    resolver = _resolver(_entry(), current_model=None)
    with pytest.raises(ModelBadRequestError, match="未配置当前模型"):
        resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.CHAT)


def test_current_model_none_but_override_present_resolves() -> None:
    # 未配置当前模型，但该用途有覆盖：走覆盖模型，不因缺当前模型报错。
    resolver = _resolver(
        _entry(provider="openai", model="gpt-4o", context_window=128000),
        current_model=None,
        overrides={RequestOrigin.PLAN: _OVERRIDE},
    )
    out = resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.PLAN)
    assert out.ref == _OVERRIDE


def test_explicit_selection_bypasses_override_but_is_validated() -> None:
    resolver = _resolver(
        _entry(),
        _entry(provider="openai", model="gpt-4o", context_window=128000),
        overrides={RequestOrigin.CHAT: _OVERRIDE},
    )
    # 即便该用途配了覆盖，显式选择仍直接采用显式 provider/model。
    out = resolver.resolve(
        ExplicitModelSelection("deepseek", "deepseek-chat"),
        origin=RequestOrigin.CHAT,
    )
    assert out.ref == _CURRENT
    # 显式选择也要经 catalog 校验：目录里没有的模型照样报错、不 fallback。
    with pytest.raises(ModelBadRequestError):
        resolver.resolve(
            ExplicitModelSelection("deepseek", "ghost"),
            origin=RequestOrigin.CHAT,
        )
