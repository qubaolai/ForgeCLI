"""恢复操作: /undo, /checkpoints, /restore (ADR-0015 §11 / §12).

恢复的第一原则是**不覆盖用户在那之后做的修改**. 所以每一项还原前都要比对 postimage:
当前内容还等于"我们改完时的样子"才能自动还原; 不等于就说明有人 (用户, 另一个工具, 或者
外部进程) 动过, 进 CONFLICTED 走预览与逐项确认, 而不是静默覆盖.

restore 自己也会建一个新的 checkpoint —— 撤销操作本身也要能撤销.

Forge 不会自动调用 `git reset --hard` 或 `git clean`: 那会连用户自己的未提交修改一起
抹掉, 而恢复的目的恰恰是保护它们.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from forgecli.application.recovery.coordinator import RecoveryUnavailableError
from forgecli.application.recovery.recovery_store import RecoveryStore
from forgecli.application.recovery.snapshot_backend import (
    SnapshotHandle,
    WorkspaceSnapshotBackend,
)
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.recovery.checkpoint import (
    RecoveryCheckpoint,
    TransactionState,
)
from forgecli.domain.recovery.mutation import (
    ConflictStatus,
    MutationEntry,
    Operation,
)
from forgecli.domain.tool.hashing import digest_text

__all__ = ["RecoveryPreview", "RecoveryService", "RestoreOutcome", "RestorePlanItem"]

_MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class RestorePlanItem:
    """一个对象的还原动作预览."""

    relative_path: str
    action: str  # restore_content / delete_created / skip_conflict / unrecoverable
    conflict: ConflictStatus
    detail: str = ""

    @property
    def applicable(self) -> bool:
        return self.action in ("restore_content", "delete_created")


@dataclass(frozen=True)
class RecoveryPreview:
    checkpoint_id: str
    items: tuple[RestorePlanItem, ...]

    @property
    def conflicted(self) -> tuple[RestorePlanItem, ...]:
        return tuple(
            item for item in self.items if item.conflict is ConflictStatus.CONFLICTED
        )

    @property
    def clean(self) -> bool:
        return not self.conflicted


@dataclass(frozen=True)
class RestoreOutcome:
    checkpoint_id: str
    restored: tuple[str, ...]
    skipped: tuple[str, ...]
    new_checkpoint_id: str | None


class RecoveryService:
    """列出, 预览与执行恢复."""

    def __init__(
        self,
        store: RecoveryStore,
        *,
        writer: Callable[[str, bytes], None],
        remover: Callable[[str], None],
        snapshots: WorkspaceSnapshotBackend | None = None,
        clock: Callable[[], str] = lambda: "",
    ) -> None:
        self._store = store
        # 真实写盘由调用方注入: 恢复层不自己碰文件系统实现, 便于测试与替换.
        self._write = writer
        self._remove = remover
        # 用整棵工作区快照建立的 checkpoint 要用同一个后端还原.
        self._snapshots = snapshots
        self._clock = clock

    def list_checkpoints(self, workspace_id: str) -> tuple[RecoveryCheckpoint, ...]:
        return self._store.list_checkpoints(workspace_id)

    def preview(
        self, checkpoint: RecoveryCheckpoint, context: ExecutionContext
    ) -> RecoveryPreview:
        items = tuple(
            self._plan_item(entry, context) for entry in checkpoint.mutations.entries
        )
        return RecoveryPreview(checkpoint_id=checkpoint.checkpoint_id, items=items)

    def restore(
        self,
        checkpoint: RecoveryCheckpoint,
        context: ExecutionContext,
        *,
        force_conflicts: bool = False,
    ) -> RestoreOutcome:
        """还原. 冲突项默认跳过, 不静默覆盖.

        两条路: checkpoint 带 snapshot_ref 时整棵工作区回到快照那一刻; 否则逐条按
        preimage 还原. 走哪条由 manifest 记的事实决定, 不由这里猜.
        """
        if checkpoint.snapshot_ref is not None:
            return self._restore_snapshot(checkpoint, context)
        preview = self.preview(checkpoint, context)
        restored: list[str] = []
        skipped: list[str] = []
        for item in preview.items:
            entry = checkpoint.mutations.find(item.relative_path)
            if entry is None:
                continue
            if not item.applicable and not force_conflicts:
                skipped.append(item.relative_path)
                continue
            absolute = self._absolute(item.relative_path, context)
            if entry.existed_before:
                self._restore_content(entry, absolute, checkpoint.workspace_id)
            else:
                self._remove(absolute)
            restored.append(item.relative_path)

        done = replace(
            checkpoint,
            status=(
                TransactionState.RESTORED
                if not skipped
                else TransactionState.CONFLICTED
            ),
        )
        self._store.save_manifest(done)
        return RestoreOutcome(
            checkpoint_id=checkpoint.checkpoint_id,
            restored=tuple(restored),
            skipped=tuple(skipped),
            # 撤销本身也要能撤销: 由调用方在还原前建立新的事务并把 id 传回来.
            new_checkpoint_id=None,
        )

    def _restore_snapshot(
        self, checkpoint: RecoveryCheckpoint, context: ExecutionContext
    ) -> RestoreOutcome:
        """整棵工作区回到快照那一刻.

        没有逐项冲突判定: 快照还原是全量替换, "某个文件被别人改过"在这里不是要跳过的
        例外, 而正是要被覆盖的东西. 这与 preimage 路径的语义不同, 所以单独一条路 ——
        把它们塞进同一个循环只会让两种语义互相污染.

        后端拿不到时抛 RecoveryUnavailableError: 声称能还原却还原不了, 比明确报错糟得多.
        """
        if self._snapshots is None or checkpoint.snapshot_ref is None:
            raise RecoveryUnavailableError(
                f"{checkpoint.checkpoint_id} 是快照式恢复点, 但当前没有可用的快照后端"
            )
        handle = SnapshotHandle(
            snapshot_id=checkpoint.checkpoint_id,
            backend=checkpoint.snapshot_backend or self._snapshots.name,
            location=checkpoint.snapshot_ref,
            root=context.primary_root,
        )
        try:
            self._snapshots.restore(handle, context.primary_root)
        except OSError as exc:
            raise RecoveryUnavailableError(f"快照还原失败: {exc}") from exc
        done = replace(checkpoint, status=TransactionState.RESTORED)
        self._store.save_manifest(done)
        return RestoreOutcome(
            checkpoint_id=checkpoint.checkpoint_id,
            restored=(context.primary_root,),
            skipped=(),
            new_checkpoint_id=None,
        )

    def crash_recovery_candidates(
        self, workspace_id: str
    ) -> tuple[RecoveryCheckpoint, ...]:
        """启动时找出可能留下部分修改的事务 (ADR-0015 §12).

        PREPARING 表示真实写入还没开始, 可以安全清理; ARMED / EXECUTING 表示可能改了
        一半, 必须让用户看到并决定.
        """
        return tuple(
            checkpoint
            for checkpoint in self._store.list_checkpoints(workspace_id)
            if checkpoint.status.may_have_partial_changes
        )

    # ---- 内部 ----

    def _plan_item(
        self, entry: MutationEntry, context: ExecutionContext
    ) -> RestorePlanItem:
        absolute = self._absolute(entry.relative_path, context)
        current = self._current_hash(absolute, context)
        if current != entry.postimage_content_hash:
            # 执行之后有人又改过: 还原会连带抹掉那次修改.
            return RestorePlanItem(
                relative_path=entry.relative_path,
                action="skip_conflict",
                conflict=ConflictStatus.CONFLICTED,
                detail="当前内容与执行结束时不一致, 期间存在后续修改",
            )
        if not entry.existed_before:
            return RestorePlanItem(
                relative_path=entry.relative_path,
                action="delete_created",
                conflict=ConflictStatus.CLEAN,
                detail="执行前该对象不存在, 撤销即删除",
            )
        if entry.preimage_content_hash is None:
            return RestorePlanItem(
                relative_path=entry.relative_path,
                action="unrecoverable",
                conflict=ConflictStatus.CLEAN,
                detail="没有保存 preimage (可再生内容或一次性无恢复执行)",
            )
        return RestorePlanItem(
            relative_path=entry.relative_path,
            action="restore_content",
            conflict=ConflictStatus.CLEAN,
        )

    def _restore_content(
        self, entry: MutationEntry, absolute: str, workspace_id: str
    ) -> None:
        if entry.preimage_content_hash is None:
            if entry.operation is Operation.DELETE:
                return
            return
        data = self._store.get_blob(workspace_id, entry.preimage_content_hash)
        self._write(absolute, data)

    def _current_hash(self, absolute: str, context: ExecutionContext) -> str | None:
        facts = context.filesystem.facts(absolute)
        if not facts.exists:
            return None
        return digest_text(context.filesystem.read_text(absolute, max_bytes=_MAX_BYTES))

    def _absolute(self, relative: str, context: ExecutionContext) -> str:
        if relative.startswith("/"):
            return relative
        return f"{context.primary_root.rstrip('/')}/{relative}"
