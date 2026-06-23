"""ConfigService：配置读取与更新用例。

职责边界：
    - effective(): 返回默认值 + 用户覆盖合并后的有效配置（只读视图）。
    - get(key):    返回某键的用户覆盖原值；未覆盖返回 None（菜单据此显示“默认”）。
    - set(key,v):  校验 -> 合并 -> 持久化。只有这一步才会写 .forge/config.toml。

ConfigService 不感知 TOML / 文件路径，全部委托给 ConfigStore 端口；
也不在 CLI 里堆叠读取 / 写入 / 合并 / 校验逻辑——这些都收敛在本服务与 config 包内。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.application.config import config_keys
from forgecli.application.config.config_store import ConfigStore
from forgecli.application.config.effective_config import EffectiveConfig
from forgecli.application.llm.model_ref import ModelRef


class ConfigService(ABC):
    """配置读取与更新端口；/config 与其他用例都只依赖它。"""

    @abstractmethod
    def effective(self) -> EffectiveConfig:
        """当前有效配置（默认值 + 覆盖）。未配置时返回全默认。"""

    @abstractmethod
    def get(self, key: str) -> str | None:
        """某键的用户覆盖原值；未覆盖返回 None。未知键抛 UnknownConfigKey。"""

    @abstractmethod
    def set(self, key: str, value: str) -> None:
        """校验并持久化一个配置项。未知键 / 非法值抛 ConfigError 子类。"""

    @abstractmethod
    def set_default_model(self, ref: ModelRef) -> None:
        """一次性持久化运行时默认模型引用。"""


class FileConfigService(ConfigService):
    """基于 ConfigStore 的默认实现。"""

    def __init__(self, store: ConfigStore) -> None:
        self._store = store

    def effective(self) -> EffectiveConfig:
        return EffectiveConfig.from_overrides(self._store.load())

    def get(self, key: str) -> str | None:
        config_keys.require_known(key)
        return self._store.load().get(key)

    def set(self, key: str, value: str) -> None:
        config_key = config_keys.require_known(key)
        canonical = config_key.validate(value)
        overrides = self._store.load()
        overrides[key] = canonical
        self._store.save(overrides)

    def set_default_model(self, ref: ModelRef) -> None:
        provider_key = config_keys.require_known(config_keys.DEFAULT_MODEL_PROVIDER_KEY)
        model_key = config_keys.require_known(config_keys.DEFAULT_MODEL_NAME_KEY)
        overrides = self._store.load()
        overrides[config_keys.DEFAULT_MODEL_PROVIDER_KEY] = provider_key.validate(
            ref.provider
        )
        overrides[config_keys.DEFAULT_MODEL_NAME_KEY] = model_key.validate(ref.model)
        self._store.save(overrides)
