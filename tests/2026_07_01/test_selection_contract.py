"""模型选择契约收敛（§3.4 / 0701）：只剩 current / explicit，旧档位彻底移除。"""

from __future__ import annotations

import dataclasses

import pytest

from forgecli.application.llm.gateway import (
    CurrentModelSelection,
    ExplicitModelSelection,
    SelectionKind,
)


def test_selection_kind_has_only_current_and_explicit() -> None:
    assert {k.value for k in SelectionKind} == {"current_model", "explicit_model"}
    assert not hasattr(SelectionKind, "TIER")


def test_tier_selection_is_no_longer_exported() -> None:
    with pytest.raises(ImportError):
        from forgecli.application.llm.gateway import TierModelSelection  # noqa: F401


def test_current_model_carries_no_provider_or_model() -> None:
    sel = CurrentModelSelection()
    assert sel.kind is SelectionKind.CURRENT_MODEL
    for forbidden in ("provider", "model", "tier", "allow_fallback", "fallback_policy"):
        assert not hasattr(sel, forbidden)


def test_explicit_model_requires_provider_and_model() -> None:
    sel = ExplicitModelSelection(provider="openai", model="gpt-4o")
    assert sel.kind is SelectionKind.EXPLICIT_MODEL
    assert (sel.provider, sel.model) == ("openai", "gpt-4o")
    with pytest.raises(ValueError):
        ExplicitModelSelection(provider="", model="gpt-4o")
    with pytest.raises(ValueError):
        ExplicitModelSelection(provider="openai", model="   ")


def test_explicit_model_has_no_fallback_fields() -> None:
    # 旧档位语义里的 allow_fallback / fallback_policy 已从 explicit 选择上删除（§3.4）。
    field_names = {f.name for f in dataclasses.fields(ExplicitModelSelection)}
    assert field_names == {"provider", "model"}
