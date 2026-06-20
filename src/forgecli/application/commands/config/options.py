"""/config 命令的配置项

加配置项 = 在这里加一个ConfigOption, 并挂到 command.py 的对应菜单分组
OptionType 目前只有 config 用，先放这里；若将来别的交互命令也要"按类型取值"，
再把它上提到交互框架(menu.py / ports.py)。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class OptionType(Enum):
    TOGGLE = auto()  # 开关 -> confirm
    TEXT = auto()  # 文本 -> ask_text
    PATH = auto()  # 路径 -> ask_text + 校验
    CHOICE = auto()  # 枚举 -> select


@dataclass(frozen=True)
class ConfigOption:
    key: str  # 配置项
    label: str  # 配置项说明
    type: OptionType
    choices: tuple[str, ...] = ()  # CHOICE专用


WORKSPACE_DIR = ConfigOption("workspace.dir", "工作区目录", OptionType.PATH)
TELEMETRY = ConfigOption("telemetry.enabled", "启用使用统计", OptionType.TOGGLE)
THEME = ConfigOption(
    "output.theme", "输出主题", OptionType.CHOICE, choices=("dark", "light")
)
LOG_LEVEL = ConfigOption(
    "log.level", "日志级别", OptionType.CHOICE, choices=("debug", "info", "warn")
)
