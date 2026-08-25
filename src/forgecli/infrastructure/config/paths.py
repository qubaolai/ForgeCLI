"""配置文件存放位置的跨平台解析。

这里解析的是 Forge home（用户级持久化根目录）：应用配置（config.json / llm.json）
与项目存储（projects/）都落在它下面，与当前工作目录无关，应当稳定。工作区目录列表
属于项目级配置（projects/<id>/forge.json），不在此处。

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
    """配置目录下某个文件的完整路径（如 config.json / llm.json）。"""
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


def projects_dir() -> Path:
    """项目存储根: ``~/.forge/projects``."""
    return config_dir() / "projects"


def user_memory_file() -> Path:
    """用户偏好记忆 (ADR-0033 决策 4). 跟人走, 不跟项目走."""
    return config_dir() / "memory.json"


def project_memory_file(project_id: str) -> Path:
    """项目事实记忆 (ADR-0033 决策 4).

    与计划不同, 它**不按会话分区**: 计划脱离产生它的那段对话就会失真, 而记忆的判据
    正好相反 —— 只记脱离对话之后仍然成立的事实 (决策 5), 那种东西才配跨会话.
    """
    return projects_dir() / project_id / "memory.json"


def plans_dir(project_id: str, session_id: str) -> Path:
    """计划与待办的存放位置 (ADR-0022 §2).

    **会话级.** 计划与待办脱离产生它们的那段对话就会失真: 一条写着"切分"的待办, 切什么,
    按什么边界, 那个信息在对话里而不在清单里. 新会话读到它只会按自己的理解填空, 而那个
    理解未必是当初的 —— 这是看不出来的漂移, 因为清单本身长得完全正常.

    ``/resume`` 回到的是**同一个** session_id, 历史由事件重建, 上下文跟着回来, 所以
    "中断后接着干"不需要靠跨会话持久化来实现.

    住在会话目录下而不是项目目录加一个 session_id 字段: 如果它只对一个会话有意义, 就该
    住在那个会话里. 附带好处是删会话时它一起走, 项目目录不会攒下孤儿计划.

    仍然不落在工作区里: 那会污染用户仓库, 还会被下一轮 Agent 当成项目内容读回上下文
    (与 state_dir 同一条理由). 落在工作区外的另一个后果是 ``fs_*`` 工具够不到它 ——
    这正是 ADR-0022 决策 5 想要的隔离.
    """
    return projects_dir() / project_id / "sessions" / session_id / "plans"


def learned_rules_file(workspace_id: str) -> Path:
    """学习规则文件 (ADR-0013 §5.1). 按项目分开, workspace 范围的规则不跨项目.

    落在 Forge 状态目录: Agent 与它起的子进程都不该能改自己的授权规则.
    """
    return state_dir() / "rules" / f"{workspace_id}.json"
