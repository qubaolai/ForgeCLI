"""工作区快照后端 (ADR-0015 §8 的 FULL 策略实现).

为什么需要它: 复制式 preimage 是**按文件**保存旧内容的, 于是"可能写到工作区任何位置"
这件事要求先复制整个工作区. 一个真实仓库轻易超过几万个文件, 于是预算检查判超, 命令被
拒 —— 而那正是最需要恢复保障的一类命令 (`npm test`, `make`, 影响范围推导不出来的程序).

写时复制 (copy-on-write) 把这件事的代价从"复制内容"变成"复制元数据": APFS 的
`clonefile`, btrfs / XFS 的 reflink 都只登记引用, 磁盘占用接近零, 时间只与文件数有关.
于是 FULL 从"通常不可用"变成"通常可用".

三条边界:

- **可用性靠实测, 不靠平台判断.** 同一台 macOS 上, 工作区与快照目录可能在不同卷上,
  那时 clonefile 直接失败. 所以探测的方式是真的克隆一个临时文件试一次.
- **拿不到就说拿不到.** 探测失败时返回 None, 由恢复层退回按文件保存 preimage, 再由
  预算检查决定要不要拒绝执行. 绝不能假装快照成功了.
- **快照不放在工作区内.** 放进去会被下一次快照递归包含, 也会污染用户的仓库.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

__all__ = ["SnapshotHandle", "WorkspaceSnapshotBackend"]


@dataclass(frozen=True)
class SnapshotHandle:
    """一份已经落地的工作区快照.

    `backend` 进 manifest: 恢复时必须用**捕获它的那个后端**去还原, 换一个后端读同一个
    位置得到的可能是别的东西.
    """

    snapshot_id: str
    backend: str
    location: str
    root: str
    file_count: int = 0


class WorkspaceSnapshotBackend(ABC):
    """整棵工作区的捕获与还原. 一次调用对应一份快照."""

    @property
    @abstractmethod
    def name(self) -> str:
        """进 manifest 的后端标识."""

    @abstractmethod
    def available_for(self, root: str) -> bool:
        """这个工作区能不能用本后端做快照. 必须实测, 不能只看平台."""

    @abstractmethod
    def capture(self, root: str, snapshot_id: str) -> SnapshotHandle:
        """捕获快照. 失败抛 OSError —— 调用方据此退回按文件保存 preimage."""

    @abstractmethod
    def restore(self, handle: SnapshotHandle, root: str) -> None:
        """把工作区还原到快照那一刻."""

    @abstractmethod
    def discard(self, handle: SnapshotHandle) -> None:
        """丢弃快照占用的空间. 保留期过后由清理流程调用."""
