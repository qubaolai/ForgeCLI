"""配置文件存放位置的跨平台解析。

这里解析的是 Forge home（用户级持久化根目录）：应用配置（config.toml / llm.toml）
与项目存储（projects/）都落在它下面，与当前工作目录无关，应当稳定。工作区目录列表
属于项目级配置（projects/<id>/forge.toml），不在此处。

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


def state_dir() -> Path:
    """Forge 状态根目录: 工具产物与工作区恢复数据落在这里.

    刻意放在 Forge home 而不是工作区: 恢复数据必须在被恢复的目录之外, 否则一条
    ``rm -rf .`` 会同时删掉工作区和用来还原它的备份 (ADR-0015 §6); 工具产物落在工作区
    则会污染用户仓库, 还会被下一轮 Agent 当成项目内容读回上下文.
    """
    return config_dir() / "state"


def artifacts_dir() -> Path:
    """超阈值工具输出的落盘位置 (ADR-0004 §8)."""
    return state_dir() / "artifacts"


def recovery_dir() -> Path:
    """RecoveryStore 根目录 (ADR-0015 §6). Agent 与 Shell 不得写入."""
    return state_dir() / "recovery"


def learned_rules_file(workspace_id: str) -> Path:
    """学习规则文件 (ADR-0013 §5.1). 按项目分开, workspace 范围的规则不跨项目.

    落在 Forge 状态目录: Agent 与它起的子进程都不该能改自己的授权规则.
    """
    return state_dir() / "rules" / f"{workspace_id}.toml"
