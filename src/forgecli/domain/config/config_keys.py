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
LOGGING_CONSOLE = "logging.console"
LOGGING_DIRECTORY = "logging.directory"
LOGGING_MAX_VALUE_CHARS = "logging.max_value_chars"
LOGGING_INCLUDE_HTTP = "logging.include_http"
EXECUTION_TOOLCHAIN_DIRS = "execution.toolchain_dirs"
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
    INT = auto()


@dataclass(frozen=True)
class ConfigKey:
    """一个配置键的封闭定义。"""

    name: str
    level: ConfigLevel
    kind: ValueKind
    default: str = ""
    choices: tuple[str, ...] = ()
    # 给人看的名字与一句说明。原先 CLI 菜单和 Web 各自写一份中文文案，同一个配置项在两处
    # 叫不同的名字；两边都不会因此报错，只会让用户以为是两个开关。文案是配置项的属性，
    # 和它的类型、默认值一样，所以住在这里。
    label: str = ""
    help: str = ""

    @property
    def title(self) -> str:
        """展示用的名字；没写标签时退回键名，而不是显示一个空白行。"""
        return self.label or self.name

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

        if self.kind is ValueKind.INT:
            try:
                number = int(text)
            except ValueError:
                raise ConfigValidationError(
                    f"配置项 {self.title} 必须是整数，收到: {value!r}"
                ) from None
            if number < 0:
                raise ConfigValidationError(f"配置项 {self.title} 不能为负")
            return str(number)

        return text  # TEXT


SCHEMA: tuple[ConfigKey, ...] = (
    # 应用级 → config.json
    ConfigKey(
        TELEMETRY_ENABLED,
        ConfigLevel.APP,
        ValueKind.BOOL,
        default="false",
        label="运行指标采集",
        help="统计各阶段的调用次数与耗时，只留在本机内存里，进程退出即丢弃；"
        "关掉之后 /diagnostics 的阶段耗时为空。",
    ),
    ConfigKey(
        OUTPUT_THEME,
        ConfigLevel.APP,
        ValueKind.CHOICE,
        default="dark",
        choices=("dark", "light"),
        label="界面主题",
        help="深色或浅色。",
    ),
    ConfigKey(
        LOGGING_LEVEL,
        ConfigLevel.APP,
        ValueKind.CHOICE,
        default="info",
        choices=("debug", "info", "warn"),
        label="日志级别",
        help="下次启动生效。debug 会把每次模型调用的请求体也写进去。",
    ),
    ConfigKey(
        LOGGING_CONSOLE,
        ConfigLevel.APP,
        ValueKind.BOOL,
        default="false",
        label="日志同时写终端",
        help="默认只写文件。开启后日志会打到 stderr，终端里的对话区会被戳花。",
    ),
    ConfigKey(
        LOGGING_DIRECTORY,
        ConfigLevel.APP,
        ValueKind.TEXT,
        label="日志目录",
        help="留空表示 Forge 主目录下的 logs/。",
    ),
    ConfigKey(
        LOGGING_MAX_VALUE_CHARS,
        ConfigLevel.APP,
        ValueKind.INT,
        label="日志单值截断长度",
        help="一条日志里单个值最多写多少字符，0 表示不截断。留空用内置上限。",
    ),
    ConfigKey(
        LOGGING_INCLUDE_HTTP,
        ConfigLevel.APP,
        ValueKind.BOOL,
        default="false",
        label="日志包含 HTTP 库",
        help="把 httpx / uvicorn 的日志收进同一个文件。排查卡在供应商上的问题时需要，"
        "平时会淹掉自己的日志。",
    ),
    ConfigKey(
        EXECUTION_TOOLCHAIN_DIRS,
        ConfigLevel.APP,
        ValueKind.TEXT,
        label="额外工具链目录",
        help="装在非标准位置的 jdk / maven / node 所在目录，按系统路径分隔符分隔。"
        "它们会进受控 PATH，但不算可信目录。",
    ),
    # 项目级 → forge.json
    ConfigKey(
        DEFAULT_MODEL_PROVIDER_KEY,
        ConfigLevel.PROJECT,
        ValueKind.TEXT,
        label="默认模型供应商",
        help="由模型选择流程写入，一般不手改。",
    ),
    ConfigKey(
        DEFAULT_MODEL_NAME_KEY,
        ConfigLevel.PROJECT,
        ValueKind.TEXT,
        label="默认模型",
        help="由模型选择流程写入，一般不手改。",
    ),
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
