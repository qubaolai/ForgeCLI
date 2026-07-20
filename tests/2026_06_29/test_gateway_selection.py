"""ModelSelection 两类型的字段互斥与校验（ADR-0011 §3.4）。"""

from __future__ import annotations

import dataclasses

import pytest

from forgecli.application.llm.gateway import (
    CurrentModelSelection,
    ExplicitModelSelection,
    ModelSelection,
    SelectionKind,
)


def test_kinds_are_distinct() -> None:
    assert CurrentModelSelection().kind is SelectionKind.CURRENT_MODEL
    assert ExplicitModelSelection("deepseek", "deepseek-chat").kind is (
        SelectionKind.EXPLICIT_MODEL
    )


def test_current_model_carries_no_explicit_fields() -> None:
    # §3.4：current_model 不得携带 provider/model；旧 tier / fallback 字段也已移除。
    # 用结构互斥保证——这些字段在 CurrentModelSelection 上根本不存在。
    sel = CurrentModelSelection()
    for forbidden in ("tier", "provider", "model", "allow_fallback", "fallback_policy"):
        assert not hasattr(sel, forbidden)


def test_explicit_requires_provider_and_model() -> None:
    with pytest.raises(ValueError):
        ExplicitModelSelection("", "deepseek-chat")
    with pytest.raises(ValueError):
        ExplicitModelSelection("deepseek", "  ")


def test_selections_are_frozen_and_subtype_of_base() -> None:
    sel = ExplicitModelSelection("deepseek", "deepseek-chat")
    assert isinstance(sel, ModelSelection)
    assert isinstance(CurrentModelSelection(), ModelSelection)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sel.model = "deepseek-reasoner"  # type: ignore[misc]
