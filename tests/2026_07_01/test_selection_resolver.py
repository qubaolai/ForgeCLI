"""ModelSelectionResolver 输入 / 输出契约冻结（ADR-0011 §2 / §3.4 / 0701）。

今日只冻结契约、不实现解析：用测试内固定解析器证明输入（selection + origin +
能力诉求）与输出（ResolvedModel）契约可被履行；真实解析在 07-02 落地。
"""

from __future__ import annotations

import dataclasses

import pytest

from forgecli.application.llm.gateway import (
    CurrentModelSelection,
    ExplicitModelSelection,
    ModelCatalogEntry,
    ModelSelection,
    ModelSelectionResolver,
    RequestOrigin,
    ResolvedModel,
)
from forgecli.application.llm.model_ref import ModelRef


class _StubResolver(ModelSelectionResolver):
    """测试内固定解析器：证明冻结的输入 / 输出契约可被履行（不含真实解析）。"""

    def __init__(self, resolved: ResolvedModel) -> None:
        self._resolved = resolved
        self.calls: list[tuple[ModelSelection, RequestOrigin]] = []

    def resolve(
        self,
        selection: ModelSelection,
        *,
        origin: RequestOrigin,
        required_capabilities: tuple[str, ...] = (),
        min_context_window: int | None = None,
    ) -> ResolvedModel:
        self.calls.append((selection, origin))
        return self._resolved


def _resolved() -> ResolvedModel:
    ref = ModelRef("openai", "gpt-4o")
    entry = ModelCatalogEntry(provider="openai", model="gpt-4o", context_window=128000)
    return ResolvedModel(ref=ref, entry=entry)


def test_resolver_is_abstract() -> None:
    with pytest.raises(TypeError):
        ModelSelectionResolver()  # type: ignore[abstract]


def test_resolved_model_carries_ref_and_entry_and_is_frozen() -> None:
    resolved = _resolved()
    assert resolved.ref == ModelRef("openai", "gpt-4o")
    assert resolved.entry.context_window == 128000
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.ref = ModelRef("openai", "other")  # type: ignore[misc]


def test_resolve_contract_accepts_current_and_explicit_with_origin() -> None:
    resolver = _StubResolver(_resolved())
    out_current = resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.CHAT)
    out_explicit = resolver.resolve(
        ExplicitModelSelection("openai", "gpt-4o"),
        origin=RequestOrigin.PLAN,
        required_capabilities=("tool_calling",),
        min_context_window=32000,
    )
    assert isinstance(out_current, ResolvedModel)
    assert isinstance(out_explicit, ResolvedModel)
    # origin 作为用途标签随调用透传，供后续按用途覆盖读取（07-02）。
    assert [origin for _, origin in resolver.calls] == [
        RequestOrigin.CHAT,
        RequestOrigin.PLAN,
    ]
