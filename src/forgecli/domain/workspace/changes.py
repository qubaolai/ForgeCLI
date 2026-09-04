"""工作区文件变化的领域值对象。

快照本身由 infrastructure 取得；这些类型只描述差异，供 agent loop、运行事件和
模型通知共用同一份语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "WorkspaceChange",
    "WorkspaceChangeKind",
    "WorkspaceChangeSource",
    "WorkspaceFileState",
    "WorkspaceSnapshot",
]


class WorkspaceChangeKind(Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


class WorkspaceChangeSource(Enum):
    AGENT = "agent"
    EXTERNAL = "external"


@dataclass(frozen=True)
class WorkspaceFileState:
    """工作区内一个文件的轻量状态指纹。

    这里不保存文件正文。size/mtime/inode 足以捕捉正常文件系统上的新增、删除、覆盖、
    重命名和内容修改，同时避免每一步都读取整个工作区。
    """

    path: str
    size: int
    mtime_ns: int
    file_identity: str = ""
    mode: int = 0


@dataclass(frozen=True)
class WorkspaceSnapshot:
    files: tuple[WorkspaceFileState, ...] = ()

    @property
    def by_path(self) -> dict[str, WorkspaceFileState]:
        return {item.path: item for item in self.files}


@dataclass(frozen=True)
class WorkspaceChange:
    path: str
    kind: WorkspaceChangeKind
    source: WorkspaceChangeSource

    @property
    def display_path(self) -> str:
        """适合放进模型通知的路径，避免恶意文件名伪造通知结构。"""

        return "".join(
            {
                "\n": "\\n",
                "\r": "\\r",
                "\t": "\\t",
            }.get(character, character if ord(character) >= 32 else "?")
            for character in self.path
        )

    @property
    def source_label(self) -> str:
        return (
            "本 agent" if self.source is WorkspaceChangeSource.AGENT else "外部参与者"
        )

    @property
    def kind_label(self) -> str:
        return {
            WorkspaceChangeKind.CREATED: "新增",
            WorkspaceChangeKind.MODIFIED: "修改",
            WorkspaceChangeKind.DELETED: "删除",
        }[self.kind]
