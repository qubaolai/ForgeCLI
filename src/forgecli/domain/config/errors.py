"""标量配置上下文的错误。

通用的「配置文件错误」基类(ConfigError / ConfigReadError / ConfigValidationError)
住在 shared.errors，被 config / models 两个上下文与 TOML 基础设施共用；
这里只定义标量配置专有的 UnknownConfigKey，并 re-export 基类供本上下文统一引用。
"""

from __future__ import annotations

from forgecli.shared.errors import (
    ConfigError,
    ConfigReadError,
    ConfigValidationError,
)

__all__ = [
    "ConfigError",
    "ConfigReadError",
    "ConfigValidationError",
    "UnknownConfigKey",
]


class UnknownConfigKey(ConfigError):
    """配置键不在应用封闭定义的可配置面内。"""
