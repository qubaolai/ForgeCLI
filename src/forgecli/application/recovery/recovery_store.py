"""RecoveryStore: 恢复数据的存储抽象 (ADR-0015 §6).

存储位置必须在**工作区之外** —— 一条 `rm -rf .` 不能既删掉工作区又删掉用来还原它的
备份. 内容按哈希寻址, 相同内容跨 checkpoint 去重.

manifest 必须原子写: 不能先改工作区再异步补记录, 否则崩在中间就得到一个"改了但没记"的
状态, 而那正是最需要恢复的时候.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.recovery.checkpoint import RecoveryCheckpoint

__all__ = ["RecoveryStore", "StoredBlob"]


class StoredBlob:
    """一段已落盘的 preimage 内容引用."""

    def __init__(self, content_hash: str, size: int) -> None:
        self.content_hash = content_hash
        self.size = size


class RecoveryStore(ABC):
    """按 workspace_id 隔离的恢复数据存储."""

    @abstractmethod
    def put_blob(self, workspace_id: str, data: bytes) -> StoredBlob:
        """内容寻址写入. 同内容重复写只占一份空间."""

    @abstractmethod
    def get_blob(self, workspace_id: str, content_hash: str) -> bytes:
        """读回 preimage. 找不到时抛 KeyError —— 静默返回空内容会把文件还原成空文件."""

    @abstractmethod
    def save_manifest(self, checkpoint: RecoveryCheckpoint) -> None:
        """原子持久化 manifest. 返回即表示已经落盘."""

    @abstractmethod
    def load_manifest(
        self, workspace_id: str, checkpoint_id: str
    ) -> RecoveryCheckpoint | None: ...

    @abstractmethod
    def list_checkpoints(self, workspace_id: str) -> tuple[RecoveryCheckpoint, ...]:
        """按创建时间倒序列出."""

    @abstractmethod
    def delete_checkpoint(self, workspace_id: str, checkpoint_id: str) -> bool:
        """只能由受控的恢复服务调用, Agent Shell 不得直接删除恢复数据."""
