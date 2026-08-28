"""注册表的 key 必须等于它自己的 spec.id (ADR-0040 §8.1).

`REGISTRY` 里曾经有一行 `"GLM": ProviderSpec(id="glm", ...)`. 两个名字不一致, 后果是分岔
的: 菜单按 key 列, 用户点一下 GLM 就往配置文件里写 `"GLM"`; 而代码里凡是拿 `spec.id` 去
查的地方一律查不到 —— `is_known_provider("glm")` 返回 False, 那家供应商等于不存在.
两条路各自都能跑通, 所以谁也不会报错.

一张手写的表里, "key 和它描述的东西同名"这种约束只能靠人眼守. 这里把它写成断言, 顺便守住
已经写出去的旧配置仍然读得回来.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.config.llm_config_store import LlmConfigStore
from forgecli.application.llm.errors import UnknownProvider


class _FrozenStore(LlmConfigStore):
    """只读的假 store: 这些用例只关心 load() 出来的 provider id 怎么被解释."""

    def __init__(self, section: dict[str, object]) -> None:
        self._section = section

    def load(self) -> dict[str, object]:
        return self._section

    def upsert_model(
        self,
        provider_id: str,
        model_id: str,
        fields: Mapping[str, object],
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        raise AssertionError("这些用例不写入")

    def remove_model(self, provider_id: str, model_id: str) -> None:
        raise AssertionError("这些用例不写入")

    def upsert_provider_field(
        self,
        provider_id: str,
        field: str,
        value: object,
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        raise AssertionError("这些用例不写入")

    def upsert_runtime_field(self, section: str, field: str, value: object) -> None:
        raise AssertionError("这些用例不写入")


def test_registry_key_equals_spec_id() -> None:
    """key 与 spec.id 是同一个事实, 不能有两份写法."""
    mismatched = {
        key: spec.id
        for key, spec in provider_registry.REGISTRY.items()
        if key != spec.id
    }
    assert mismatched == {}


def test_registry_keys_are_already_normalized() -> None:
    """归一函数对 key 本身必须是恒等的, 否则 `REGISTRY[normalize(k)]` 会打空."""
    for key in provider_registry.REGISTRY:
        assert provider_registry.normalize_provider_id(key) == key


def test_lookup_accepts_the_id_from_spec() -> None:
    """从 spec.id 出发一定查得回同一个 spec —— 这条正是 "GLM" 破坏掉的."""
    for spec in provider_registry.REGISTRY.values():
        assert provider_registry.is_known_provider(spec.id)
        assert provider_registry.require_known_provider(spec.id) is spec


def test_legacy_uppercase_id_still_resolves() -> None:
    """旧配置文件里存着 "GLM", 换 key 不能让那些文件突然认不出来."""
    assert provider_registry.is_known_provider("GLM")
    assert provider_registry.require_known_provider("GLM").id == "glm"


def test_unknown_provider_still_raises() -> None:
    """归一只处理大小写, 不是把未知供应商放行."""
    assert not provider_registry.is_known_provider("no-such-provider")
    with pytest.raises(UnknownProvider):
        provider_registry.require_known_provider("no-such-provider")


def test_config_normalizes_legacy_provider_key() -> None:
    """读配置时归一一次, 下游拿到的是注册表的 key 而不是文件里的写法."""
    service = LlmConfigService(
        _FrozenStore({"providers": {"GLM": {"models": {"glm-4": {}}}}})
    )
    providers = service.providers()
    assert [provider.id for provider in providers] == ["glm"]
    assert [model.provider for model in providers[0].models] == ["glm"]
