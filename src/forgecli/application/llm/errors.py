"""LLM 上下文的错误。

通用基类来自 shared.errors；这里只定义 LLM 专有的 UnknownProvider，
并 re-export 基类供本上下文（配置切片、将来的调用切片）统一引用。
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
    "UnknownProvider",
]


class UnknownProvider(ConfigValidationError):
    """配置 / 写入引用了未实现的供应商（不在封闭 ProviderRegistry 内）。"""


class InvalidModelRef(ConfigValidationError):
    """模型引用非法（provider / name 为空等）。"""
