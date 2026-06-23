"""配置文件存放位置的跨平台解析。

这里解析的是 Forge home（用户级持久化根目录）：应用配置（config.toml / llm.toml）
与项目存储（projects/）都落在它下面，与当前工作目录无关，应当稳定。工作区目录列表
属于项目级配置（projects/<id>/project.toml），不在此处。

解析顺序（「可配置项」即此处的覆盖入口）：
    1. 环境变量 FORGE_CONFIG_DIR —— 显式覆盖（支持 ~ 展开）；
    2. 默认 ~/.forge —— 用户主目录下。

跨平台：Path.home() 在 macOS / Linux / Windows 上都能解析到当前用户主目录
（Windows 即 C:\\Users\\<name>），因此默认位置天然适配多操作系统。
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_DIR_ENV = "FORGE_CONFIG_DIR"
_DEFAULT_DIRNAME = ".forge"


def config_dir() -> Path:
    """解析配置文件目录：环境变量优先，否则用户主目录下的 ~/.forge。"""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / _DEFAULT_DIRNAME


def config_file(name: str) -> Path:
    """配置目录下某个文件的完整路径（如 config.toml / llm.toml）。"""
    return config_dir() / name
