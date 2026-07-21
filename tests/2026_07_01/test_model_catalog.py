"""ModelCatalogService 只读视图接口 + ModelCatalogEntry 元数据边界（§4 / 0701）。

今日只冻结接口与 DTO：用测试内最小 stub 子类证明「单模型能力唯一事实来源」契约可用，
真实的 TOML 合并视图留给后续切片。
"""

from __future__ import annotations

import dataclasses

import pytest

from forgecli.application.llm.gateway import (
    ModelBadRequestError,
    ModelCatalogEntry,
    ModelCatalogService,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.thinking import (
    ModelThinkingCapabilities,
    ModelThinkingSettings,
    ThinkingMode,
)


class _StubCatalog(ModelCatalogService):
    """测试内最小只读视图：由固定 entries 支撑，不解析任何配置。"""

    def __init__(self, *entries: ModelCatalogEntry) -> None:
        self._by_ref = {ModelRef(e.provider, e.model): e for e in entries}

    def has_model(self, ref: ModelRef) -> bool:
        return ref in self._by_ref

    def get(self, ref: ModelRef) -> ModelCatalogEntry:
        entry = self._by_ref.get(ref)
        if entry is None:
            raise ModelBadRequestError(
                f"未知模型 {ref}", provider=ref.provider, model=ref.model
            )
        return entry


def _entry(
    *,
    provider: str = "openai",
    model: str = "gpt-4o",
    context_window: int = 128000,
    max_output_tokens: int | None = None,
    supports_structured_output: bool = False,
    supports_tool_calling: bool = False,
    allowlisted: bool = True,
    deprecated: bool = False,
) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        provider=provider,
        model=model,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_structured_output=supports_structured_output,
        supports_tool_calling=supports_tool_calling,
        thinking_capabilities=ModelThinkingCapabilities(),
        thinking_settings=ModelThinkingSettings(mode=ThinkingMode.OFF),
        allowlisted=allowlisted,
        deprecated=deprecated,
    )


def test_service_is_abstract() -> None:
    with pytest.raises(TypeError):
        ModelCatalogService()  # type: ignore[abstract]


def test_has_model_and_get_round_trip() -> None:
    entry = _entry(supports_tool_calling=True, supports_structured_output=True)
    catalog = _StubCatalog(entry)
    ref = ModelRef("openai", "gpt-4o")
    assert catalog.has_model(ref) is True
    got = catalog.get(ref)
    assert got is entry
    assert got.context_window == 128000
    assert got.supports_tool_calling is True
    assert got.supports_structured_output is True


def test_get_unknown_model_raises_bad_request() -> None:
    catalog = _StubCatalog(_entry())
    unknown = ModelRef("openai", "does-not-exist")
    assert catalog.has_model(unknown) is False
    with pytest.raises(ModelBadRequestError):
        catalog.get(unknown)


def test_entry_is_frozen() -> None:
    entry = _entry()
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.context_window = 1  # type: ignore[misc]


def test_entry_validates_fields() -> None:
    with pytest.raises(ValueError):
        _entry(provider="")
    with pytest.raises(ValueError):
        _entry(model="  ")
    with pytest.raises(ValueError):
        _entry(context_window=0)
    with pytest.raises(ValueError):
        _entry(max_output_tokens=0)


def test_entry_capability_defaults_are_conservative() -> None:
    entry = _entry()
    assert entry.supports_structured_output is False
    assert entry.supports_tool_calling is False
    assert entry.thinking_mode is ThinkingMode.OFF
    assert entry.allowlisted is True
    assert entry.deprecated is False
    assert entry.max_output_tokens is None
