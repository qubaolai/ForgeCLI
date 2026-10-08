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

from forgecli.common.runtime_files import runtime_dir

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
    """持久状态根目录: 恢复快照、凭证冷却等跨运行数据; 临时产物不放这里."""
    return config_dir() / "state"


def artifacts_dir() -> Path:
    """可丢弃的通用工具产物, 使用进程级系统临时目录."""
    return runtime_dir() / "artifacts"


def runtime_project_dir(project_id: str) -> Path:
    """当前运行中某个项目的临时产物根目录."""
    return runtime_dir() / "projects" / project_id


def session_runtime_dir(project_id: str, session_id: str) -> Path:
    """工具输出、诊断与子 Agent 临时记录; 不存会话恢复真相源."""
    return runtime_project_dir(project_id) / "sessions" / session_id


def recovery_dir() -> Path:
    """FsRecoveryStore 根目录 (ADR-0015 §6). Agent 与 Shell 不得写入."""
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


# ---- 原 infrastructure/platform_paths.py ----
# 不信任进程环境变量的本机平台目录探测。
_CSIDL = {
    "program_files": 0x26,
    "program_data": 0x23,
    "profile": 0x28,
}


def windows_known_directory(kind: str) -> str | None:
    """通过 Windows API 取目录；非 Windows 或 API 失败时返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        windll = ctypes.windll  # type: ignore[attr-defined]
        if kind == "windows":
            length = windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
            return buffer.value if 0 < length < len(buffer) else None
        csidl = _CSIDL.get(kind)
        if csidl is None:
            return None
        result = windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer)
        return buffer.value if result == 0 and buffer.value else None
    except (AttributeError, OSError, ValueError):
        return None


def windows_protected_directories() -> tuple[str, ...]:
    """至少覆盖 Windows、Program Files 与 ProgramData 的真实 OS 位置。"""
    values = (
        windows_known_directory("windows"),
        windows_known_directory("program_files"),
        windows_known_directory("program_data"),
    )
    return tuple(dict.fromkeys(value for value in values if value))
