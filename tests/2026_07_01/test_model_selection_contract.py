"""2026-07-01: ADR-0011 model selection and catalog contracts."""

from __future__ import annotations

import inspect

import pytest

import forgecli.application.llm.gateway as gateway
from forgecli.application.llm.gateway import (
    CurrentModelSelection,
    ExplicitModelSelection,
    ModelCatalogEntry,
    ModelSelectionResolver,
    RequestOrigin,
    SelectionKind,
)


def test_current_model_selection_carries_no_provider_or_model() -> None:
    selection = CurrentModelSelection()

    assert selection.kind is SelectionKind.CURRENT_MODEL
    assert not hasattr(selection, "provider")
    assert not hasattr(selection, "model")


def test_explicit_model_selection_requires_provider_and_model() -> None:
    selection = ExplicitModelSelection(provider="deepseek", model="deepseek-chat")

    assert selection.kind is SelectionKind.EXPLICIT_MODEL
    assert selection.provider == "deepseek"
    assert selection.model == "deepseek-chat"

    with pytest.raises(ValueError, match="provider 与 model"):
        ExplicitModelSelection(provider="", model="deepseek-chat")
    with pytest.raises(ValueError, match="provider 与 model"):
        ExplicitModelSelection(provider="deepseek", model=" ")


def test_old_tier_selection_api_is_unavailable() -> None:
    assert SelectionKind.__members__ == {
        "CURRENT_MODEL": SelectionKind.CURRENT_MODEL,
        "EXPLICIT_MODEL": SelectionKind.EXPLICIT_MODEL,
    }
    assert "TierModelSelection" not in gateway.__all__
    assert not hasattr(gateway, "TierModelSelection")
    assert not hasattr(CurrentModelSelection(), "allow_fallback")
    assert not hasattr(
        ExplicitModelSelection(provider="deepseek", model="deepseek-chat"),
        "fallback_policy",
    )


def test_model_catalog_entry_validates_single_model_metadata_boundary() -> None:
    entry = ModelCatalogEntry(
        provider="deepseek",
        model="deepseek-chat",
        context_window=65536,
        max_output_tokens=8192,
        supports_structured_output=True,
        supports_tool_calling=True,
        supports_thinking=True,
        allowlisted=True,
        deprecated=False,
    )

    assert entry.provider == "deepseek"
    assert entry.model == "deepseek-chat"
    assert entry.context_window == 65536
    assert entry.supports_structured_output is True
    assert entry.supports_tool_calling is True
    assert entry.supports_thinking is True

    with pytest.raises(ValueError, match="provider 与 model"):
        ModelCatalogEntry(provider="", model="deepseek-chat", context_window=65536)
    with pytest.raises(ValueError, match="context_window"):
        ModelCatalogEntry(provider="deepseek", model="deepseek-chat", context_window=0)
    with pytest.raises(ValueError, match="max_output_tokens"):
        ModelCatalogEntry(
            provider="deepseek",
            model="deepseek-chat",
            context_window=65536,
            max_output_tokens=0,
        )


def test_model_selection_resolver_contract_keeps_origin_as_input_label() -> None:
    signature = inspect.signature(ModelSelectionResolver.resolve)

    assert tuple(signature.parameters) == (
        "self",
        "selection",
        "origin",
        "required_capabilities",
        "min_context_window",
    )
    assert signature.parameters["origin"].kind is inspect.Parameter.KEYWORD_ONLY
    assert RequestOrigin.CHAT.value == "chat"
