"""WorkspaceMutationCoordinator: 首次破坏性写入屏障 (ADR-0015 §1 / §4 / §8).

它回答的问题只有一个: **这次真实工作区写入具备恢复保障吗.**

它不回答"允不允许做"(那是 ToolAuthorizationService), 也不回答"进程能触达什么"(那是
隔离层). 恢复层可以拒绝建立恢复保障, 但**不能**把安全层的 ASK / DENY 提升为允许.

写入顺序是这个类存在的全部意义:

    校验当前对象身份 -> 保存 preimage -> manifest 原子落盘 -> ARMED -> 才允许真实写入

顺序反过来, 崩在中间就没有旧内容可还原了. 因此 `arm` 之前调用 `record_write` 会直接
抛错, 而不是"先记一下待会补".
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import PurePath

from forgecli.application.recovery.path_state import directory_content_hash
from forgecli.application.recovery.recovery_store import RecoveryStore
from forgecli.application.recovery.snapshot_backend import (
    SnapshotHandle,
    WorkspaceSnapshotBackend,
)
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathKind
from forgecli.domain.recovery.checkpoint import (
    RecoveryCheckpoint,
    RecoveryPolicy,
    SnapshotStrategy,
    TransactionState,
)
from forgecli.domain.recovery.mutation import (
    MutationEntry,
    ObjectType,
    Operation,
    Recoverability,
    is_destructive,
)
from forgecli.domain.tool.hashing import digest, digest_text
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.workspace.boundary import is_within
from forgecli.shared.errors import ForgeError

__all__ = [
    "MutationTransaction",
    "RecoveryUnavailableError",
    "WorkspaceMutationCoordinator",
]

_MAX_PREIMAGE_BYTES = 64 * 1024 * 1024
# FULL 快照的文件数上限. 超过就让预算检查判超, 由 begin 抛 RecoveryUnavailableError:
# 一个大仓库不该在这里静默走完几十万次 stat, 也不该被"保护了一部分"糊过去.
_MAX_FULL_FILES = 20_000


class RecoveryUnavailableError(ForgeError):
    """无法建立恢复保障. 协调器据此返回 recovery_unavailable observation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MutationTransaction:
    """一次 ToolInvocation 对应的恢复事务.

    复合 Shell 命令算**一个**事务: 不能给管道或 `&&` 的每个分析单元各建一个互不关联的
    恢复点, 否则撤销时得逐个猜哪些属于同一次操作.
    """

    def __init__(
        self,
        checkpoint: RecoveryCheckpoint,
        store: RecoveryStore,
        context: ExecutionContext,
        policy: RecoveryPolicy,
        *,
        clock: Callable[[], str],
    ) -> None:
        self._checkpoint = checkpoint
        self._store = store
        self._context = context
        self._policy = policy
        self._clock = clock

    @property
    def checkpoint(self) -> RecoveryCheckpoint:
        return self._checkpoint

    @property
    def checkpoint_id(self) -> str:
        return self._checkpoint.checkpoint_id

    def arm(self) -> RecoveryCheckpoint:
        """manifest 原子落盘并进入 ARMED. 在这之前任何真实破坏性写入都是违规的."""
        self._checkpoint = replace(self._checkpoint, status=TransactionState.ARMED)
        self._store.save_manifest(self._checkpoint)
        return self._checkpoint

    def record_write(self, absolute_path: str, operation: Operation) -> MutationEntry:
        """在真实写入**之前**调用. 保存 preimage 并把 manifest 落盘.

        返回后才允许真的动这个文件.
        """
        if not self._checkpoint.armed:
            raise RecoveryUnavailableError(
                f"事务处于 {self._checkpoint.status.value}, 此时不允许破坏性写入"
            )
        relative = self._relative(absolute_path)
        if self._checkpoint.mutations.find(relative) is not None:
            # 同一事务内第二次改同一个路径: 保留最初的 preimage, 不重复存.
            existing = self._checkpoint.mutations.find(relative)
            assert existing is not None
            return existing

        facts = self._context.filesystem.facts(absolute_path)
        existed = facts.exists
        regenerable = self._policy.is_regenerable(relative)
        entry = MutationEntry(
            relative_path=relative,
            object_type=_object_type(facts.kind.value),
            existed_before=existed,
            operation=operation,
            file_identity=facts.file_identity,
            mode=facts.mode,
            link_target=facts.link_target,
            regenerable=regenerable,
            recoverability=(
                Recoverability.CREATE_ONLY if not existed else Recoverability.FULL
            ),
        )
        if is_destructive(operation, existed_before=existed) and not regenerable:
            entry = self._store_preimage(entry, absolute_path, facts.file_identity)
        self._append(entry)
        return entry

    def record_result(
        self, absolute_path: str, *, deleted: bool = False
    ) -> MutationEntry | None:
        """写入完成后记录 postimage. 撤销时靠它判断"这中间有没有别人动过"."""
        relative = self._relative(absolute_path)
        entry = self._checkpoint.mutations.find(relative)
        if entry is None:
            return None
        postimage = None if deleted else self._hash_of(absolute_path)
        facts = self._context.filesystem.facts(absolute_path)
        updated = replace(
            entry,
            object_type=(
                _object_type(facts.kind.value) if facts.exists else entry.object_type
            ),
            postimage_content_hash=postimage,
            postimage_metadata_hash=self._metadata_hash(absolute_path),
        )
        self._append(updated)
        return updated

    def complete(self, *, failed: bool = False) -> RecoveryCheckpoint:
        state = TransactionState.FAILED if failed else TransactionState.COMPLETED
        self._checkpoint = replace(self._checkpoint, status=state)
        self._store.save_manifest(self._checkpoint)
        return self._checkpoint

    # ---- 内部 ----

    def _store_preimage(
        self, entry: MutationEntry, absolute_path: str, file_identity: str
    ) -> MutationEntry:
        """保存旧内容. **拿不到完整内容时抛错, 绝不落一个空 preimage.**

        原来这里无条件相信 read_bytes 的返回值, 而 OsFileSystemView 在读失败时 (目录,
        权限不足, 读到一半被删) 会吞掉 OSError 返回 `b""`. 于是 checkpoint 里
        preimage_content_hash 是"空内容的哈希", recoverability 却标着 FULL ——
        声称可恢复而实际什么都没存, 是恢复层最坏的一种失败.
        """
        facts = self._context.filesystem.facts(absolute_path)
        if facts.kind is PathKind.DIRECTORY:
            return replace(
                entry,
                preimage_metadata_hash=digest(
                    {"mode": facts.mode, "file_identity": facts.file_identity}
                ),
            )
        if not facts.is_regular_file:
            raise RecoveryUnavailableError(
                f"无法为 {absolute_path} 保存旧内容: 它不是普通文件 "
                f"({facts.kind.value})"
            )
        if facts.size > _MAX_PREIMAGE_BYTES:
            raise RecoveryUnavailableError(
                f"{absolute_path} 超过单文件 preimage 上限 "
                f"({facts.size} > {_MAX_PREIMAGE_BYTES})"
            )
        data = self._context.filesystem.read_bytes(
            absolute_path, max_bytes=_MAX_PREIMAGE_BYTES
        )
        if len(data) != facts.size:
            raise RecoveryUnavailableError(
                f"{absolute_path} 的旧内容读取不完整 "
                f"(读到 {len(data)} 字节, 期望 {facts.size})"
            )
        blob = self._store.put_blob(self._checkpoint.workspace_id, data)
        return replace(
            entry,
            preimage_content_hash=blob.content_hash,
            preimage_metadata_hash=digest({"file_identity": file_identity}),
        )

    def _append(self, entry: MutationEntry) -> None:
        self._checkpoint = replace(
            self._checkpoint,
            mutations=self._checkpoint.mutations.with_entry(entry),
            status=TransactionState.EXECUTING,
        )
        # 每次都重写 manifest: 崩溃点可能落在任意两次写入之间.
        self._store.save_manifest(self._checkpoint)

    def _relative(self, absolute_path: str) -> str:
        r"""相对主工作区根的路径. 按路径段比较, 不做字符串前缀匹配.

        字符串前缀会把 `/w2/x` 判成不在 `/w` 内 (正确), 但也会把 `/workspace-backup`
        判成在 `/workspace` 内 (错误) —— 全仓其他地方都用 is_within, 这里不该例外.

        **必须用平台原生 PurePath, 不能写死 PurePosixPath.** 后者在 Windows 上不把
        反斜杠当分隔符, 于是 `\\psf\Home\proj\a.py` 被当成一整个文件名: is_within
        (走原生 PureWindowsPath) 说"在根内", 紧接着 relative_to 说"不在根内", 同一个
        函数里两行自相矛盾, 抛 ValueError. Parallels 共享目录这类 UNC 路径必踩.

        relative_to 仍可能因为两侧锚点不同而抛错 (例如一个是 UNC 一个是盘符), 那时
        退回绝对路径: 恢复清单里多一条绝对路径只是不好看, 而抛错会让整次写入失败.
        """
        root = self._context.primary_root
        if not is_within(absolute_path, root):
            return absolute_path
        try:
            return str(PurePath(absolute_path).relative_to(PurePath(root)))
        except ValueError:
            return absolute_path

    def _hash_of(self, absolute_path: str) -> str | None:
        facts = self._context.filesystem.facts(absolute_path)
        if not facts.exists:
            return None
        if facts.kind is PathKind.DIRECTORY:
            return directory_content_hash(absolute_path, self._context)
        return digest_text(
            self._context.filesystem.read_text(
                absolute_path, max_bytes=_MAX_PREIMAGE_BYTES
            )
        )

    def _metadata_hash(self, absolute_path: str) -> str | None:
        facts = self._context.filesystem.facts(absolute_path)
        if not facts.exists:
            return None
        return digest({"mode": facts.mode, "file_identity": facts.file_identity})


class WorkspaceMutationCoordinator:
    """按计划选择快照策略并建立恢复事务."""

    def __init__(
        self,
        store: RecoveryStore,
        *,
        policy: RecoveryPolicy | None = None,
        snapshots: WorkspaceSnapshotBackend | None = None,
        clock: Callable[[], str] = lambda: "",
        id_factory: Callable[[], str] = lambda: f"ckpt_{uuid.uuid4().hex[:12]}",
    ) -> None:
        self._store = store
        self._policy = RecoveryPolicy() if policy is None else policy
        # 有快照后端时 FULL 走整棵工作区快照; 没有就退回逐文件 preimage, 再由预算检查
        # 决定要不要拒绝执行. 缺省 None = 没有 —— "以为有快照"比"知道没有"危险得多.
        self._snapshots = snapshots
        self._clock = clock
        self._new_id = id_factory

    @property
    def policy(self) -> RecoveryPolicy:
        return self._policy

    def prune_expired(self, workspace_id: str, *, now_epoch: float) -> int:
        """清掉过了保留期的恢复点, 返回清理条数.

        快照式恢复点必须有清理, 而复制式的其实也一样 —— 只是快照更迫切: 一份写时复制
        快照占的磁盘接近零, 但**inode 不是零**, 每次 FULL 都留一份, 一个大仓库跑几百次
        就把卷的 inode 用光. `retention_seconds` 早先只是 RecoveryPolicy 上一个没有任何
        执行点的字段, 现在它是这件事的判据.
        """
        deadline = now_epoch - self._policy.retention_seconds
        removed = 0
        for checkpoint in self._store.list_checkpoints(workspace_id):
            if _created_epoch(checkpoint.created_at) > deadline:
                continue
            if checkpoint.snapshot_ref is not None and self._snapshots is not None:
                self._snapshots.discard(
                    SnapshotHandle(
                        snapshot_id=checkpoint.checkpoint_id,
                        backend=checkpoint.snapshot_backend or self._snapshots.name,
                        location=checkpoint.snapshot_ref,
                        root="",
                    )
                )
            if self._store.delete_checkpoint(workspace_id, checkpoint.checkpoint_id):
                removed += 1
        return removed

    def strategy_for(self, plan: ToolPlan) -> SnapshotStrategy:
        """按计划的目标封闭程度选策略 (ADR-0015 §8).

        没有 Overlay 可用时只剩两种可能: 目标可靠就 TARGETED, 不可靠就 FULL. 而 FULL
        建不起来时 auto 不能继续 —— 这一条由 `begin` 抛错来保证.
        """
        if not plan.mutates_workspace:
            return SnapshotStrategy.NONE
        if plan.target_resolution.closed and plan.effects.mutating_targets:
            return SnapshotStrategy.TARGETED
        return SnapshotStrategy.FULL

    def begin(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        *,
        workspace_id: str,
        session_id: str,
        turn_id: str,
        policy_version: str,
    ) -> MutationTransaction | None:
        """建立恢复事务. 不需要恢复保障时返回 None.

        建不起来时抛 RecoveryUnavailableError, 而不是返回一个"其实没保障"的事务 ——
        checkpoint 创建失败时绝不能声称操作可恢复 (ADR-0015 §10).
        """
        strategy = self.strategy_for(plan)
        if strategy is SnapshotStrategy.NONE:
            return None
        if not strategy.available:
            raise RecoveryUnavailableError(f"{strategy.value} 策略当前不可用")
        if strategy is SnapshotStrategy.FULL and not self._policy.full_snapshot_allowed:
            raise RecoveryUnavailableError(
                "写入目标未封闭, 需要完整 checkpoint, 但当前策略不允许"
            )

        checkpoint_id = self._new_id()
        snapshot = self._capture_snapshot(strategy, context, checkpoint_id)
        targets: tuple[str, ...] = ()
        if snapshot is None:
            # 没有快照后端: 退回逐文件 preimage, 于是要过文件数与字节数预算.
            targets = self._planned_targets(plan, context, strategy)
            if not self._policy.within_budget(
                files=len(targets), total_bytes=self._estimate(targets, context)
            ):
                raise RecoveryUnavailableError(
                    f"恢复预算不足: 这次调用可能写到工作区任何位置, 需要保护 "
                    f"{len(targets)} 个对象, 超出 {self._policy.max_checkpoint_files} "
                    "的上限. 复制式 preimage 存不下这么大的范围, 而这个工作区所在的"
                    "文件系统不支持写时复制快照 —— 要么把写入目标写明确 (改用 fs.* "
                    "或给出具体路径), 要么把工作区放到支持 reflink / clonefile 的卷上."
                )

        checkpoint = RecoveryCheckpoint(
            checkpoint_id=checkpoint_id,
            workspace_id=workspace_id,
            session_id=session_id,
            turn_id=turn_id,
            tool_invocation_id=plan.plan_id,
            command_plan_hash=plan.plan_hash,
            created_at=self._clock(),
            # 先 PREPARING: 这一刻还不允许任何真实写入.
            status=TransactionState.PREPARING,
            snapshot_strategy=strategy,
            policy_version=policy_version,
            execution_profile_hash=context.execution_profile_hash,
            snapshot_ref=snapshot.location if snapshot is not None else None,
            snapshot_backend=snapshot.backend if snapshot is not None else None,
        )
        transaction = MutationTransaction(
            checkpoint, self._store, context, self._policy, clock=self._clock
        )

        transaction.arm()
        # FULL 策略要在进程启动前就把范围内的内容全部保护住 (§8.4): 没有写屏障的后端
        # 事后补不了 preimage. 已经有整棵工作区快照时这一步不需要 —— 快照本身就是它.
        if strategy is SnapshotStrategy.FULL and snapshot is None:
            for path in targets:
                transaction.record_write(path, Operation.OVERWRITE)
        return transaction

    def _capture_snapshot(
        self,
        strategy: SnapshotStrategy,
        context: ExecutionContext,
        checkpoint_id: str,
    ) -> SnapshotHandle | None:
        """FULL 策略下尝试捕获整棵工作区快照. 拿不到返回 None.

        只对 FULL 做: TARGETED 已经有精确目标, 为几个文件快照整棵树是纯浪费.

        捕获失败**不抛错**, 而是退回逐文件 preimage —— 快照只是一条更便宜的路, 不是
        唯一的路. 但也绝不能失败之后假装成功: 返回 None 之后调用方会照常过预算检查,
        该拒绝的仍然会被拒绝.
        """
        if strategy is not SnapshotStrategy.FULL or self._snapshots is None:
            return None
        try:
            return self._snapshots.capture(context.primary_root, checkpoint_id)
        except OSError:
            return None

    def _planned_targets(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        strategy: SnapshotStrategy,
    ) -> tuple[str, ...]:
        if strategy is SnapshotStrategy.TARGETED:
            return plan.effects.mutating_targets
        # FULL: 整个主工作区都是潜在影响面. 审批展示要如实说明这一点.
        #
        # 必须**递归**列到普通文件. 原来只列了顶层一层, 而顶层项大多是目录 ——
        # 目录进不了 preimage, 于是每个子树一个字节都没被保护, checkpoint 却照样
        # 标成可恢复.
        return _walk_files(context, context.primary_root, self._policy)

    def _estimate(self, targets: tuple[str, ...], context: ExecutionContext) -> int:
        return sum(context.filesystem.facts(path).size for path in targets)


def _walk_files(
    context: ExecutionContext, root: str, policy: RecoveryPolicy
) -> tuple[str, ...]:
    """递归列出 root 下需要 preimage 的普通文件.

    跳过三类:

    - 符号链接. 跟着链接读到的是目标的内容, 而撤销要还原的是链接本身, 两者不是一回事.
    - 可再生内容 (`__pycache__/`, `dist/`, 各种 cache). `record_write` 本来就不给它们
      存 preimage, 放进来只会白占预算.
    - 走到 `_MAX_FULL_FILES` 之后的部分. 停下来**不是**"就保护这么多": 返回的条数会让
      `within_budget` 判超, 于是 begin 抛 RecoveryUnavailableError —— 保护不了整个
      工作区时, 结论必须是"建不起恢复点", 不能是"保护一部分算了".
    """
    found: list[str] = []
    pending = [root]
    while pending and len(found) <= _MAX_FULL_FILES:
        current = pending.pop()
        for name in context.filesystem.list_dir(current):
            child = f"{current.rstrip('/')}/{name}"
            facts = context.filesystem.facts(child)
            if facts.is_symlink:
                continue
            relative = child[len(root.rstrip("/")) + 1 :] if child != root else ""
            if facts.kind is PathKind.DIRECTORY:
                if not policy.is_regenerable(f"{relative}/"):
                    pending.append(child)
                continue
            if facts.is_regular_file and not policy.is_regenerable(relative):
                found.append(child)
                if len(found) > _MAX_FULL_FILES:
                    break
    return tuple(sorted(found))


def _object_type(kind: str) -> ObjectType:
    mapping = {
        "file": ObjectType.FILE,
        "directory": ObjectType.DIRECTORY,
        "missing": ObjectType.MISSING,
    }
    return mapping.get(kind, ObjectType.FILE)


def _created_epoch(created_at: str) -> float:
    """manifest 里的创建时间转 epoch. 解析不了就当成"很久以前".

    方向是有意的: 读不懂时间戳时宁可清掉一个恢复点, 也不要让一份读不懂的记录永久占住
    inode —— 而真正需要它的场景 (刚出事就撤销) 时间戳一定是新写的, 不会走到这里.
    """
    try:
        return datetime.fromisoformat(created_at).timestamp()
    except (TypeError, ValueError):
        return 0.0
