"""项目与工作区的领域词汇。

对应 ADR-0008 的首启信任模型: 一个项目 = 一个受信任的主工作区根 + 若干经 /add-dir
追加的目录。这些概念与配置存成 TOML 还是别的格式无关, 故属领域。

ProjectContext 不在这里: 它是进程内可变的"当前项目"持有者, 属编排设施。
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
    """单个项目的**状态**快照：信任与工作区目录（会话事件 27 日再接入）。

    项目级*配置偏好*（如 logging.level）不在此——它们由 ConfigService 经统一 SCHEMA
    读写 forge.toml，与本状态在同一文件、各写各的键（round-trip 互不覆盖）。
    """

    project_id: str
    trusted: bool
    primary_workspace_root: str
    # 始终至少包含 primary_workspace_root，且其为首元素。
    workspace_roots: tuple[str, ...]


@dataclass(frozen=True)
class IndexEntry:
    """项目索引里的一条：已信任根目录(规范路径) -> project-id。"""

    root: str  # 规范绝对路径，同时是 index.toml 中的 key
    project_id: str
    trusted: bool
    created_at: str
    updated_at: str


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
