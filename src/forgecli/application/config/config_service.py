"""ConfigService：统一的配置读取与更新用例（按 level 路由）。

一个泛型 set/get/display：查 SCHEMA 拿到 ConfigKey 的 `level`，路由到该级对应的
ConfigStore（应用级 → config.json，项目级 → forge.json）。"写哪个文件"由数据决定，
不再为某个配置项写专门的 setter——加配置项只需在 SCHEMA 加一条（满足开闭原则）。

ConfigService 不感知 TOML / 文件路径，全部委托给 ConfigStore；校验由 SCHEMA 的
ConfigKey 负责。项目级 store 在装配时绑定当前项目的 forge.json；未绑定时对项目级键
的访问会显式报错而不是误落应用文件。
"""

from __future__ import annotations

from forgecli.application.config.config_store import ConfigStore
from forgecli.domain.config import config_keys
from forgecli.domain.config.config_keys import ConfigKey, ConfigLevel
from forgecli.domain.config.effective_config import EffectiveConfig
from forgecli.domain.config.errors import ConfigValidationError


class ConfigService:
    """配置读取与更新用例；/config 与其他用例都依赖它。"""

    def __init__(self, app: ConfigStore, project: ConfigStore | None = None) -> None:
        # level -> store。项目级 store 可选：纯应用上下文（无项目）可不提供。
        self._stores: dict[ConfigLevel, ConfigStore] = {ConfigLevel.APP: app}
        if project is not None:
            self._stores[ConfigLevel.PROJECT] = project

    def _store(self, key: ConfigKey) -> ConfigStore:
        store = self._stores.get(key.level)
        if store is None:
            raise ConfigValidationError(
                f"配置项 {key.name} 的 {key.level.name} 级存储未装配"
            )
        return store

    def effective(self) -> EffectiveConfig:
        """当前有效配置视图：应用级 + 当前项目级覆盖。"""
        overrides: dict[str, str] = {}
        for level in ConfigLevel:
            store = self._stores.get(level)
            if store is not None:
                overrides.update(store.load())
        return EffectiveConfig.from_overrides(overrides)

    def get(self, key: str) -> str | None:
        """某键的用户覆盖原值；未覆盖返回 None。"""
        config_key = config_keys.require_known(key)
        return self._store(config_key).load().get(key)

    def display(self, key: str) -> str:
        """某键当前的有效取值（覆盖优先，否则默认）。供菜单展示。"""
        config_key = config_keys.require_known(key)
        raw = self._store(config_key).load().get(key)
        return raw if raw is not None else config_key.default

    def set(self, key: str, value: str) -> None:
        """校验并持久化一个配置项；按 level 路由到对应文件。

        未知键 / 非法值抛 ConfigError 子类；项目级 store 未装配也抛错，不误落应用文件。
        """
        config_key = config_keys.require_known(key)
        store = self._store(config_key)
        overrides = store.load()
        overrides[key] = config_key.validate(value)
        store.save(overrides)
