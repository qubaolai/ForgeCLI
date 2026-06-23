"""项目领域的小型值对象与帮助函数（ADR-0008）。

集中放置「项目」这一概念里轻量、无副作用的部分，保持紧凑：
    - ProjectConfig：对应 ``projects/<project-id>/project.toml``。
    - IndexEntry：   对应 ``projects/index.toml`` 里一条 ``[trusted_roots."<path>"]``。
    - ProjectContext：进程内持有“当前项目”，供 /add-dir 更新、/status 读取。
    - WorkspaceError：工作区目录非法的面向用户错误。
    - generate_project_id：由规范路径生成稳定的 project-id。

设计约束与 domain/intents、config/effective_config 一致：值对象 frozen、无副作用。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from forgecli.shared.errors import ForgeError

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_HASH_LEN = 8


@dataclass(frozen=True)
class ProjectConfig:
    """单个项目的配置与状态快照（会话事件 27 日再接入）。"""

    project_id: str
    trusted: bool
    primary_workspace_root: str
    # 始终至少包含 primary_workspace_root，且其为首元素。
    workspace_roots: tuple[str, ...]
    log_level: str = "info"


@dataclass(frozen=True)
class IndexEntry:
    """项目索引里的一条：已信任根目录(规范路径) -> project-id。"""

    root: str  # 规范绝对路径，同时是 index.toml 中的 key
    project_id: str
    trusted: bool
    created_at: str
    updated_at: str


@dataclass
class ProjectContext:
    """进程内可变的当前项目持有者。

    REPL 装配时建立一次；/add-dir 写入新的 ProjectConfig，/status 读取它。
    用一个薄持有者而非把项目塞进 SessionState，是为了不让会话态耦合项目存储。
    """

    project: ProjectConfig


class WorkspaceError(ForgeError):
    """工作区目录非法（空、不存在、不是目录）。message 可直接展示给用户。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def generate_project_id(canonical_root: Path) -> str:
    """由规范绝对路径生成稳定 project-id，形如 ``ForgeCLI-a1b2c3d4``。

    前缀取目录名（便于人眼辨认），后缀取规范路径 sha256 截断（避免同名目录冲突）。
    入参须为已 resolve 的绝对路径，hash 才稳定可复现。
    """
    digest = hashlib.sha256(str(canonical_root).encode("utf-8")).hexdigest()[:_HASH_LEN]
    name = _UNSAFE.sub("-", canonical_root.name).strip("-")
    return f"{name or 'root'}-{digest}"
