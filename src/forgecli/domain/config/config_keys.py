"""可配置项的封闭定义（统一 SCHEMA）。

这里是配置面的*唯一权威登记*：所有**可设置的配置偏好**——无论应用级还是项目级——都在
SCHEMA 里声明一条 ConfigKey，带 `level` 区分归属。新增配置项 = 加一条；键名、类型、
默认值、允许取值、属于哪一级都从这里读。落盘按 level 路由（应用级 → config.json，
项目级 → forge.json）由 ConfigService 据 `level` 完成，"写哪个文件"是数据而非代码分叉。

注意：trust 标记、工作区目录等是**项目状态**而非配置偏好——它们由 ProjectService /
`/add-dir` 等领域流程管理，不进本 SCHEMA、不经通用 set。

凭证保护就是这份白名单本身：SCHEMA 不收录凭证字段，require_known() 据此拒绝任何不在
表内的键，凭证（api key 等）也就无法被 /config 写入明文配置文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from forgecli.domain.config.errors import (
    ConfigValidationError,
    UnknownConfigKey,
)

# ---- 键名常量（dotted key 与配置文件表结构对应）----
# 应用级（config.json）
TELEMETRY_ENABLED = "telemetry.enabled"
OUTPUT_THEME = "output.theme"
LOGGING_LEVEL = "logging.level"
# 项目级（forge.json）
DEFAULT_MODEL_PROVIDER_KEY = "model.provider"
DEFAULT_MODEL_NAME_KEY = "model.name"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigLevel(Enum):
    """配置项归属：应用级（跨项目）或项目级（随项目）。决定落盘到哪个文件。"""

    APP = auto()
    PROJECT = auto()


class ValueKind(Enum):
    """配置取值的业务类型，决定如何归一化与校验。"""

    BOOL = auto()
    CHOICE = auto()
    TEXT = auto()


@dataclass(frozen=True)
class ConfigKey:
    """一个配置键的封闭定义。"""

    name: str
    level: ConfigLevel
    kind: ValueKind
    default: str = ""
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

        return text  # TEXT


SCHEMA: tuple[ConfigKey, ...] = (
    # 应用级 → config.json
    ConfigKey(TELEMETRY_ENABLED, ConfigLevel.APP, ValueKind.BOOL, default="false"),
    ConfigKey(
        OUTPUT_THEME,
        ConfigLevel.APP,
        ValueKind.CHOICE,
        default="dark",
        choices=("dark", "light"),
    ),
    ConfigKey(
        LOGGING_LEVEL,
        ConfigLevel.APP,
        ValueKind.CHOICE,
        default="info",
        choices=("debug", "info", "warn"),
    ),
    # 项目级 → forge.json
    ConfigKey(DEFAULT_MODEL_PROVIDER_KEY, ConfigLevel.PROJECT, ValueKind.TEXT),
    ConfigKey(DEFAULT_MODEL_NAME_KEY, ConfigLevel.PROJECT, ValueKind.TEXT),
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


def keys_for(level: ConfigLevel) -> tuple[ConfigKey, ...]:
    """某一级的全部配置键，供按级查询 / 路由 / 菜单使用。"""
    return tuple(key for key in SCHEMA if key.level is level)
