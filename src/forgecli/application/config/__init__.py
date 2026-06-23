"""配置应用层：默认值、有效配置视图、配置键约束（封闭白名单）、更新用例。

对外只暴露 ConfigService 端口与值对象；TOML 文件读写属于 infrastructure，
本包不感知文件格式与磁盘细节。
"""

from __future__ import annotations

from forgecli.application.config.config_keys import ConfigKey
from forgecli.application.config.config_service import ConfigService
from forgecli.application.config.config_store import ConfigStore
from forgecli.application.config.effective_config import EffectiveConfig
from forgecli.application.config.errors import (
    ConfigError,
    ConfigReadError,
    ConfigValidationError,
    UnknownConfigKey,
)

__all__ = [
    "ConfigKey",
    "ConfigService",
    "ConfigStore",
    "EffectiveConfig",
    "ConfigError",
    "ConfigReadError",
    "ConfigValidationError",
    "UnknownConfigKey",
]
