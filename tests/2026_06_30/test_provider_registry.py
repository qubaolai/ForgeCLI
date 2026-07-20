"""ProviderRegistry：代码侧 adapter 绑定 + 封闭集准入（2026-06-30）。"""

from __future__ import annotations

import pytest

from forgecli.application.llm.errors import UnknownProvider
from forgecli.application.llm.gateway import ModelUnavailableError, ProviderRegistry
from forgecli.infrastructure.llm.adapters import FakeModelProvider


def test_register_and_get_returns_adapter() -> None:
    registry = ProviderRegistry()
    adapter = FakeModelProvider(provider_id="deepseek")
    registry.register(adapter)
    assert registry.get("deepseek") is adapter


def test_get_unknown_provider_raises_unknown_provider() -> None:
    with pytest.raises(UnknownProvider):
        ProviderRegistry().get("does_not_exist")


def test_register_unknown_provider_id_raises_unknown_provider() -> None:
    # 代码注册也必须落在封闭集内：配置 / 外部无法注入任意 provider 类名。
    with pytest.raises(UnknownProvider):
        ProviderRegistry().register(FakeModelProvider(provider_id="totally_unknown"))


def test_duplicate_registration_raises_value_error() -> None:
    registry = ProviderRegistry()
    registry.register(FakeModelProvider(provider_id="deepseek"))
    with pytest.raises(ValueError):
        registry.register(FakeModelProvider(provider_id="deepseek"))


def test_known_but_unregistered_raises_model_unavailable() -> None:
    # deepseek 属封闭集，但未绑定 adapter：与「未知 provider」区分开。
    with pytest.raises(ModelUnavailableError):
        ProviderRegistry().get("deepseek")


def test_consolidated_provider_ids_are_registrable() -> None:
    # 并轨新增的 openai / local 已进封闭集，可注册可路由。
    registry = ProviderRegistry()
    for pid in ("openai", "local"):
        adapter = FakeModelProvider(provider_id=pid)
        registry.register(adapter)
        assert registry.get(pid) is adapter
