"""恢复点与恢复策略 (ADR-0015 §3 / §5 / §7 / §10).

事务状态机的关键是 ARMED 这一档: **preimage 持久化完成之前, 一次真实的破坏性写入都不
允许发生.** 反过来的顺序 (先写再补记录) 看起来只差几毫秒, 但那几毫秒里进程一崩, 旧内容
就永远回不来了.

快照策略这一版只实现 NONE / TARGETED / FULL. OVERLAY 依赖沙箱的临时写层, 沙箱本次没做,
因此它的枚举值保留但不可选中 —— 留着是为了让将来接入隔离方案时, 策略选择这一层不用改
形状; 现在选中它会直接报错, 而不是悄悄退化成别的策略.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.recovery.mutation import MutationSet
from forgecli.domain.tool.hashing import digest

__all__ = [
    "RecoveryCheckpoint",
    "RecoveryPolicy",
    "SnapshotStrategy",
    "TransactionState",
]


class TransactionState(Enum):
    PREPARING = "preparing"  # 正在建立恢复元数据, 尚不允许真实破坏性写入
    ARMED = "armed"  # 恢复数据已持久化, 可以开始真实写入
    EXECUTING = "executing"  # 工具正在执行, 新触达的路径可以继续追加 preimage
    COMPLETED = "completed"
    FAILED = "failed"  # 执行失败, 但恢复点保留
    RESTORING = "restoring"
    RESTORED = "restored"
    CONFLICTED = "conflicted"  # 恢复时发现对象已被后续操作改过
    EXPIRED = "expired"

    @property
    def allows_destructive_write(self) -> bool:
        return self in (TransactionState.ARMED, TransactionState.EXECUTING)

    @property
    def may_have_partial_changes(self) -> bool:
        """崩溃恢复时用: 这个状态下可能已经发生了部分修改 (ADR-0015 §12)."""
        return self in (TransactionState.ARMED, TransactionState.EXECUTING)


class SnapshotStrategy(Enum):
    NONE = "none"
    TARGETED = "targeted"
    OVERLAY = "overlay"  # 需要沙箱临时写层, 本次未实现
    FULL = "full"

    @property
    def available(self) -> bool:
        return self is not SnapshotStrategy.OVERLAY


@dataclass(frozen=True)
class RecoveryPolicy:
    """恢复预算 (ADR-0015 §10). auto 要执行破坏性写入, 就必须先满足它."""

    max_checkpoint_files: int = 5000
    max_checkpoint_bytes: int = 256 * 1024 * 1024
    retention_seconds: float = 7 * 24 * 3600
    full_snapshot_allowed: bool = True
    # 只能来自 Forge 内置策略或用户显式配置; 模型不能自己把目录标成可再生.
    regenerable_paths: tuple[str, ...] = (
        ".pytest_cache/",
        "coverage/",
        "dist/",
        "build/",
        "node_modules/.cache/",
        "__pycache__/",
        ".ruff_cache/",
        ".mypy_cache/",
    )

    def is_regenerable(self, relative_path: str) -> bool:
        """可再生内容只记路径与操作, 不存内容.

        注意 ignore 规则**不**自动等于可再生: node_modules, 虚拟环境和本地数据库都可能
        被 ignore, 但重建它们要么很贵要么不可能.
        """
        normalized = relative_path.lstrip("./")
        return any(
            normalized.startswith(marker) or f"/{marker}" in f"/{normalized}"
            for marker in self.regenerable_paths
        )

    def within_budget(self, *, files: int, total_bytes: int) -> bool:
        return files <= self.max_checkpoint_files and (
            total_bytes <= self.max_checkpoint_bytes
        )


@dataclass(frozen=True)
class RecoveryCheckpoint:
    """一次事务的恢复点 (ADR-0015 §5)."""

    checkpoint_id: str
    workspace_id: str
    session_id: str
    turn_id: str
    tool_invocation_id: str
    command_plan_hash: str
    created_at: str
    status: TransactionState
    snapshot_strategy: SnapshotStrategy
    policy_version: str
    execution_profile_hash: str
    mutations: MutationSet = field(default_factory=MutationSet)
    # FULL 策略靠整棵工作区快照兜底时, 这里记下那份快照的引用. 有它就不需要逐文件
    # preimage —— 也正因为这样, 它必须进 manifest_hash: 恢复走哪条路由它决定.
    snapshot_ref: str | None = None
    snapshot_backend: str | None = None
    recoverable: bool = True
    incomplete_reason: str | None = None
    retention_deadline: str | None = None

    def __post_init__(self) -> None:
        if self.recoverable and self.incomplete_reason:
            raise ValueError("标了不完整原因就不能同时声称可恢复")

    @property
    def manifest_hash(self) -> str:
        """manifest 的完整性校验值. 真实写入前必须已经原子持久化."""
        return digest(
            {
                "checkpoint_id": self.checkpoint_id,
                "workspace_id": self.workspace_id,
                "tool_invocation_id": self.tool_invocation_id,
                "command_plan_hash": self.command_plan_hash,
                "snapshot_strategy": self.snapshot_strategy,
                "policy_version": self.policy_version,
                "execution_profile_hash": self.execution_profile_hash,
                "mutations": self.mutations.entries,
                "snapshot_ref": self.snapshot_ref,
                "snapshot_backend": self.snapshot_backend,
            }
        )

    @property
    def armed(self) -> bool:
        return self.status.allows_destructive_write

    def to_payload(self) -> dict[str, object]:
        """审计事件用的摘要. 只有路径级信息, 不含文件明文."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "workspace_id": self.workspace_id,
            "tool_invocation_id": self.tool_invocation_id,
            "status": self.status.value,
            "snapshot_strategy": self.snapshot_strategy.value,
            "recoverable": self.recoverable,
            "incomplete_reason": self.incomplete_reason,
            "manifest_hash": self.manifest_hash,
            "paths": [entry.relative_path for entry in self.mutations.entries],
        }
