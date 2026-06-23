"""ConfigService：配置读取与更新用例。

职责边界：
    - effective(): 返回默认值 + 用户覆盖合并后的有效配置（只读视图）。
    - get(key):    返回某键的用户覆盖原值；未覆盖返回 None（菜单据此显示“默认”）。
    - set(key,v):  校验 -> 合并 -> 持久化。只有这一步才会写 config.toml。

ConfigService 不感知 TOML / 文件路径，全部委托给 ConfigStore（application 拥有的存储
抽象，由 infrastructure 实现）；也不在 CLI 里堆叠读取 / 写入 / 合并 / 校验逻辑——
这些都收敛在本服务与 config 包内。只有一个实现，故不再单设抽象基类。
"""

from __future__ import annotations

from forgecli.application.config import config_keys
from forgecli.application.config.config_store import ConfigStore
from forgecli.application.config.effective_config import EffectiveConfig
from forgecli.application.llm.model_ref import ModelRef


class ConfigService:
    """配置读取与更新用例；/config 与其他用例都依赖它。"""

    def __init__(self, store: ConfigStore) -> None:
        self._store = store

    def effective(self) -> EffectiveConfig:
        """当前有效配置（默认值 + 覆盖）。未配置时返回全默认。"""
        return EffectiveConfig.from_overrides(self._store.load())

    def get(self, key: str) -> str | None:
        """某键的用户覆盖原值；未覆盖返回 None。未知键抛 UnknownConfigKey。"""
        config_keys.require_known(key)
        return self._store.load().get(key)

    def set(self, key: str, value: str) -> None:
        """校验并持久化一个配置项。未知键 / 非法值抛 ConfigError 子类。"""
        config_key = config_keys.require_known(key)
        canonical = config_key.validate(value)
        overrides = self._store.load()
        overrides[key] = canonical
        self._store.save(overrides)

    def set_default_model(self, ref: ModelRef) -> None:
        """一次性持久化运行时默认模型引用。"""
        provider_key = config_keys.require_known(config_keys.DEFAULT_MODEL_PROVIDER_KEY)
        model_key = config_keys.require_known(config_keys.DEFAULT_MODEL_NAME_KEY)
        overrides = self._store.load()
        overrides[config_keys.DEFAULT_MODEL_PROVIDER_KEY] = provider_key.validate(
            ref.provider
        )
        overrides[config_keys.DEFAULT_MODEL_NAME_KEY] = model_key.validate(ref.model)
        self._store.save(overrides)
