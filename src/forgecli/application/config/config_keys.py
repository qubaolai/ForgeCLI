"""可配置项的封闭定义（schema）。

这里是配置面的*唯一权威来源*：键名、类型、默认值、允许取值。
新增可配置项 = 在 SCHEMA 增加一条 ConfigKey；EffectiveConfig / 默认值 / 校验
都由 schema 推导，避免在多处重复定义键名与取值范围。

凭证保护就是这份白名单本身：SCHEMA 不收录凭证字段，require_known() 据此拒绝
任何不在表内的键，凭证（api key 等）也就无法被 /config 写入明文配置文件。
凭证应走环境变量 / keychain，不属于本文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from forgecli.application.config.errors import (
    ConfigValidationError,
    UnknownConfigKey,
)

# ---- 键名常量（dotted key，与 config.toml 的表结构对应）----
# 工作区与日志级别已移交项目级配置（ADR-0008），不再属于应用配置面。
TELEMETRY_ENABLED = "telemetry.enabled"
OUTPUT_THEME = "output.theme"
DEFAULT_MODEL_PROVIDER_KEY = "model.provider"
DEFAULT_MODEL_NAME_KEY = "model.name"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ValueKind(Enum):
    """配置取值的业务类型，决定如何归一化与校验。"""

    TEXT = auto()
    BOOL = auto()
    CHOICE = auto()


@dataclass(frozen=True)
class ConfigKey:
    """一个配置键的封闭定义。"""

    name: str
    kind: ValueKind
    default: str
    choices: tuple[str, ...] = ()

    def validate(self, value: str) -> str:
        """把用户输入归一化成可持久化的规范字符串；非法时抛 ConfigValidationError。

        归一化结果稳定可复现（同一输入恒等输出），保证配置写入可重复、可验证。
        """
        text = value.strip()
        if not text:
            raise ConfigValidationError(f"配置项 {self.name} 不能为空")

        if self.kind is ValueKind.BOOL:
            low = text.lower()
            if low in _TRUE:
                return "true"
            if low in _FALSE:
                return "false"
            raise ConfigValidationError(
                f"配置项 {self.name} 只接受是 / 否（true/false），收到: {value!r}"
            )

        if self.kind is ValueKind.CHOICE:
            if text not in self.choices:
                allowed = " / ".join(self.choices)
                raise ConfigValidationError(
                    f"配置项 {self.name} 只能是 [{allowed}]，收到: {value!r}"
                )
            return text

        return text


SCHEMA: tuple[ConfigKey, ...] = (
    ConfigKey(TELEMETRY_ENABLED, ValueKind.BOOL, default="false"),
    ConfigKey(
        OUTPUT_THEME, ValueKind.CHOICE, default="dark", choices=("dark", "light")
    ),
    ConfigKey(DEFAULT_MODEL_PROVIDER_KEY, ValueKind.TEXT, default=""),
    ConfigKey(DEFAULT_MODEL_NAME_KEY, ValueKind.TEXT, default=""),
)

_BY_NAME: dict[str, ConfigKey] = {key.name: key for key in SCHEMA}


def is_known(name: str) -> bool:
    """名字是否落在封闭可配置面（白名单）内。"""
    return name in _BY_NAME


def require_known(name: str) -> ConfigKey:
    """取键定义；不在封闭可配置面时抛 UnknownConfigKey。"""
    key = _BY_NAME.get(name)
    if key is None:
        raise UnknownConfigKey(f"未知配置项: {name}")
    return key
