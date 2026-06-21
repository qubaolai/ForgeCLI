"""/config 菜单项（纯 UI 元数据）。

这里只描述“菜单上显示什么标签、对应哪个配置键”。配置键的类型、默认值、
允许取值与校验都属于业务，住在 application/config/keys.py，菜单不复制这些定义。
新增可配置项：先在 keys.SCHEMA 注册键，再在这里挂一个 MenuOption
并加进 command.py 的分组。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.application.config import keys


@dataclass(frozen=True)
class MenuOption:
    label: str  # 菜单展示文案
    key: str  # 对应 keys.SCHEMA 中的配置键名


WORKSPACE_DIR = MenuOption("工作区目录", keys.WORKSPACE_DIR)
TELEMETRY = MenuOption("启用使用统计", keys.TELEMETRY_ENABLED)
THEME = MenuOption("输出主题", keys.OUTPUT_THEME)
LOG_LEVEL = MenuOption("日志级别", keys.LOG_LEVEL)
