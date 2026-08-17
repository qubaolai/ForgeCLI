"""恢复层的领域词汇 (ADR-0015): 恢复点, 变更集, 事务状态与恢复预算."""

from forgecli.domain.recovery.checkpoint import (
    RecoveryCheckpoint,
    RecoveryPolicy,
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
    is_destructive,
)

__all__ = [
    "ConflictStatus",
    "MutationEntry",
    "MutationSet",
    "ObjectType",
    "Operation",
    "Recoverability",
    "RecoveryCheckpoint",
    "RecoveryPolicy",
    "SnapshotStrategy",
    "TransactionState",
    "is_destructive",
]
