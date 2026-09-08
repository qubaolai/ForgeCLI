"""文件系统上的内容寻址 RecoveryStore (ADR-0015 §6).

落在 Forge 状态目录下, 按 workspace_id 隔离:

    ~/.forge/state/recovery/<workspace_id>/
        blobs/<ab>/<hash>        内容寻址, 跨 checkpoint 去重
        manifests/<id>.json      原子写入的事务清单

两个实现细节直接对应 ADR 的硬性要求:

- **manifest 先写临时文件再 rename**: 崩在半路时读到的要么是旧版本要么是新版本,
  不会是半个 JSON.
- **仅当前用户可读**: 恢复数据就是工作区内容本身, 权限放宽等于把源码副本摊开.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from forgecli.application.recovery.recovery_store import RecoveryStore, StoredBlob
from forgecli.domain.recovery.checkpoint import (
    RecoveryCheckpoint,
    SnapshotStrategy,
    TransactionState,
)
from forgecli.domain.recovery.mutation import (
    ConflictStatus,
    MutationEntry,
    MutationSet,
    ObjectType,
    Operation,
    Recoverability,
)
from forgecli.domain.tool.hashing import digest_text
from forgecli.infrastructure.json_io import write_atomic

__all__ = ["FsRecoveryStore"]

_DIR_MODE = 0o700
_FILE_MODE = 0o600


class FsRecoveryStore(RecoveryStore):
    def __init__(self, root: Path) -> None:
        self._root = root

    def put_blob(self, workspace_id: str, data: bytes) -> StoredBlob:
        content_hash = digest_text(data.decode("utf-8", errors="replace"))
        target = self._blob_path(workspace_id, content_hash)
        if not target.exists():
            self._ensure_dir(target.parent)
            write_atomic(target, data, mode=_FILE_MODE)
        return StoredBlob(content_hash=content_hash, size=len(data))

    def get_blob(self, workspace_id: str, content_hash: str) -> bytes:
        target = self._blob_path(workspace_id, content_hash)
        if not target.exists():
            # 静默返回空内容会把文件"恢复"成空文件, 比不恢复更糟.
            raise KeyError(f"恢复内容不存在: {content_hash}")
        return target.read_bytes()

    def save_manifest(self, checkpoint: RecoveryCheckpoint) -> None:
        target = self._manifest_path(checkpoint.workspace_id, checkpoint.checkpoint_id)
        self._ensure_dir(target.parent)
        write_atomic(
            target,
            json.dumps(_to_json(checkpoint), ensure_ascii=False, indent=2),
            mode=_FILE_MODE,
        )

    def load_manifest(
        self, workspace_id: str, checkpoint_id: str
    ) -> RecoveryCheckpoint | None:
        target = self._manifest_path(workspace_id, checkpoint_id)
        if not target.exists():
            return None
        return _from_json(json.loads(target.read_text(encoding="utf-8")))

    def list_checkpoints(self, workspace_id: str) -> tuple[RecoveryCheckpoint, ...]:
        directory = self._workspace_dir(workspace_id) / "manifests"
        if not directory.is_dir():
            return ()
        loaded = [
            _from_json(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(directory.glob("*.json"))
        ]
        return tuple(sorted(loaded, key=lambda item: item.created_at, reverse=True))

    def delete_checkpoint(self, workspace_id: str, checkpoint_id: str) -> bool:
        target = self._manifest_path(workspace_id, checkpoint_id)
        if not target.exists():
            return False
        target.unlink()
        return True

    def prune_orphan_blobs(self, workspace_id: str) -> int:
        """标记-清除: 先收集还活着的清单引用了哪些内容, 再删其余的 blob。

        只有 ``preimage_content_hash`` 会成为 blob —— postimage 的哈希是拿工作区现状
        算出来的, 从来没有落过盘 (见 coordinator 的 ``put_blob`` 唯一调用点)。
        """
        blobs_root = self._workspace_dir(workspace_id) / "blobs"
        if not blobs_root.is_dir():
            return 0
        alive = {
            entry.preimage_content_hash.split(":", 1)[-1]
            for checkpoint in self.list_checkpoints(workspace_id)
            for entry in checkpoint.mutations.entries
            if entry.preimage_content_hash
        }
        removed = 0
        for shard in blobs_root.iterdir():
            if not shard.is_dir():
                continue
            for blob in shard.iterdir():
                if blob.is_file() and blob.name not in alive:
                    blob.unlink()
                    removed += 1
            # 空分片目录留着只会让下次扫描多走一圈; 非空时 rmdir 自己会拒绝.
            with contextlib.suppress(OSError):
                shard.rmdir()
        return removed

    # ---- 路径 ----

    def _workspace_dir(self, workspace_id: str) -> Path:
        return self._root / workspace_id

    def _blob_path(self, workspace_id: str, content_hash: str) -> Path:
        digest_part = content_hash.split(":", 1)[-1]
        return (
            self._workspace_dir(workspace_id) / "blobs" / digest_part[:2] / digest_part
        )

    def _manifest_path(self, workspace_id: str, checkpoint_id: str) -> Path:
        return self._workspace_dir(workspace_id) / "manifests" / f"{checkpoint_id}.json"

    @staticmethod
    def _ensure_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, _DIR_MODE)


def _to_json(checkpoint: RecoveryCheckpoint) -> dict[str, object]:
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "workspace_id": checkpoint.workspace_id,
        "session_id": checkpoint.session_id,
        "turn_id": checkpoint.turn_id,
        "tool_invocation_id": checkpoint.tool_invocation_id,
        "command_plan_hash": checkpoint.command_plan_hash,
        "created_at": checkpoint.created_at,
        "status": checkpoint.status.value,
        "snapshot_strategy": checkpoint.snapshot_strategy.value,
        "policy_version": checkpoint.policy_version,
        "execution_profile_hash": checkpoint.execution_profile_hash,
        # 快照引用必须落盘: 少了它, 重启后读回来的 checkpoint 会以为自己是复制式的,
        # 于是既不会用快照还原, 清理时也不会去回收那份快照占的 inode.
        "snapshot_ref": checkpoint.snapshot_ref,
        "snapshot_backend": checkpoint.snapshot_backend,
        "recoverable": checkpoint.recoverable,
        "incomplete_reason": checkpoint.incomplete_reason,
        "retention_deadline": checkpoint.retention_deadline,
        "manifest_hash": checkpoint.manifest_hash,
        "mutations": [_entry_json(entry) for entry in checkpoint.mutations.entries],
    }


def _entry_json(entry: MutationEntry) -> dict[str, object]:
    return {
        "relative_path": entry.relative_path,
        "object_type": entry.object_type.value,
        "existed_before": entry.existed_before,
        "operation": entry.operation.value,
        "preimage_content_hash": entry.preimage_content_hash,
        "preimage_metadata_hash": entry.preimage_metadata_hash,
        "postimage_content_hash": entry.postimage_content_hash,
        "postimage_metadata_hash": entry.postimage_metadata_hash,
        "source_path": entry.source_path,
        "target_path": entry.target_path,
        "file_identity": entry.file_identity,
        "mode": entry.mode,
        "link_target": entry.link_target,
        "recoverability": entry.recoverability.value,
        "conflict_status": entry.conflict_status.value,
        "regenerable": entry.regenerable,
    }


def _from_json(data: dict[str, object]) -> RecoveryCheckpoint:
    raw_mutations = data.get("mutations", [])
    entries = (
        tuple(_entry_from_json(item) for item in raw_mutations)
        if isinstance(raw_mutations, list)
        else ()
    )
    return RecoveryCheckpoint(
        checkpoint_id=str(data["checkpoint_id"]),
        workspace_id=str(data["workspace_id"]),
        session_id=str(data.get("session_id", "")),
        turn_id=str(data.get("turn_id", "")),
        tool_invocation_id=str(data.get("tool_invocation_id", "")),
        command_plan_hash=str(data.get("command_plan_hash", "")),
        created_at=str(data.get("created_at", "")),
        status=TransactionState(str(data.get("status", "completed"))),
        snapshot_strategy=SnapshotStrategy(str(data.get("snapshot_strategy", "none"))),
        policy_version=str(data.get("policy_version", "1")),
        execution_profile_hash=str(data.get("execution_profile_hash", "")),
        mutations=MutationSet(entries=entries),
        snapshot_ref=_optional(data.get("snapshot_ref")),
        snapshot_backend=_optional(data.get("snapshot_backend")),
        recoverable=bool(data.get("recoverable", True)),
        incomplete_reason=_optional(data.get("incomplete_reason")),
        retention_deadline=_optional(data.get("retention_deadline")),
    )


def _entry_from_json(item: object) -> MutationEntry:
    if not isinstance(item, dict):
        raise ValueError("manifest 中的变更条目格式非法")
    return MutationEntry(
        relative_path=str(item["relative_path"]),
        object_type=ObjectType(str(item.get("object_type", "file"))),
        existed_before=bool(item.get("existed_before", False)),
        operation=Operation(str(item.get("operation", "overwrite"))),
        preimage_content_hash=_optional(item.get("preimage_content_hash")),
        preimage_metadata_hash=_optional(item.get("preimage_metadata_hash")),
        postimage_content_hash=_optional(item.get("postimage_content_hash")),
        postimage_metadata_hash=_optional(item.get("postimage_metadata_hash")),
        source_path=_optional(item.get("source_path")),
        target_path=_optional(item.get("target_path")),
        file_identity=str(item.get("file_identity", "")),
        mode=int(item.get("mode", 0)),
        link_target=_optional(item.get("link_target")),
        recoverability=Recoverability(str(item.get("recoverability", "full"))),
        conflict_status=ConflictStatus(str(item.get("conflict_status", "clean"))),
        regenerable=bool(item.get("regenerable", False)),
    )


def _optional(value: object) -> str | None:
    return None if value is None else str(value)
