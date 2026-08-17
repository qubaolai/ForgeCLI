"""工作区恢复层 (ADR-0015).

它是与安全裁决, 隔离并列的**第三层**: 前两者回答"允不允许"和"能触达什么", 这一层回答
"改了还能不能还原". 三层互不替代 —— 规则 ALLOW 不代表可恢复, 恢复点建好了也不代表
允许执行.
"""

from forgecli.application.recovery.coordinator import (
    MutationTransaction,
    RecoveryUnavailableError,
    WorkspaceMutationCoordinator,
)
from forgecli.application.recovery.recovery_service import (
    RecoveryPreview,
    RecoveryService,
    RestoreOutcome,
    RestorePlanItem,
)
from forgecli.application.recovery.recovery_store import RecoveryStore, StoredBlob

__all__ = [
    "MutationTransaction",
    "RecoveryPreview",
    "RecoveryService",
    "RecoveryStore",
    "RecoveryUnavailableError",
    "RestoreOutcome",
    "RestorePlanItem",
    "StoredBlob",
    "WorkspaceMutationCoordinator",
]
