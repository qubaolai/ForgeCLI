"""跨层通用异常基类。

ForgeError 是所有 forge 领域错误的根，便于上层(REPL)统一兜底。
ConfigError 及其子类是「面向用户的配置错误」：message 可直接展示给用户、
不暴露 traceback。它们被标量配置(application/config)、模型配置(application/models)
两个上下文以及 TOML 基础设施共用，因此住在 shared，而不归属任一上下文。

各上下文专有的错误(UnknownConfigKey / UnknownProvider)仍定义在各自上下文内，
继承这里的基类。
"""

from __future__ import annotations


class ForgeError(Exception):
    """所有 forge 领域错误的根基类。"""


class ConfigError(ForgeError):
    """面向用户的配置错误基类；message 可直接展示。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigReadError(ConfigError):
    """读取 / 解析配置文件失败(语法错误、编码错误、IO 错误)。"""


class ConfigValidationError(ConfigError):
    """配置取值非法(不在允许范围、空值等)。"""
