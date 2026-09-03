"""恢复事务的建立与收尾 (ADR-0015 §1 / §4).

从 ToolRequestCoordinator 里分出来的一段: 它只回答"这次写入有没有恢复保障", 不参与
裁决, 也不决定要不要执行. 顺序是它存在的全部意义 —— 先建立恢复保障, 再签发授权;
反过来就会出现"已经拿到执行授权, 但还原不了"的窗口.
"""

from __future__ import annotations

from forgecli.application.recovery.coordinator import (
    MutationTransaction,
    RecoveryUnavailableError,
    WorkspaceMutationCoordinator,
)
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.recovery.mutation import Operation
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult, ToolResultStatus

__all__ = ["RecoveryFlow"]


class RecoveryFlow:
    """把一次调用的写入目标登记进恢复事务, 并在执行后记录实际结果."""

    def __init__(
        self,
        mutations: WorkspaceMutationCoordinator | None,
        workspace_id: str,
    ) -> None:
        # 恢复层缺省为 None = 不建立恢复事务. 这不是"跳过安全检查": 需要恢复保障的写入
        # 会因为拿不到恢复层而在 begin 里直接抛 RecoveryUnavailableError.
        self._mutations = mutations
        self._workspace_id = workspace_id

    def begin(
        self, plan: ToolPlan, context: ExecutionContext, policy: PolicyContext
    ) -> MutationTransaction | None:
        """为真实工作区写入建立恢复事务, 并在屏障后保存 preimage."""
        if self._mutations is None:
            if plan.mutates_workspace:
                # 需要恢复保障却没有恢复层: fail closed, 不能"先跑再说".
                raise RecoveryUnavailableError(
                    "该调用会修改真实工作区, 但当前没有可用的恢复层"
                )
            return None
        transaction = self._mutations.begin(
            plan,
            context,
            workspace_id=self._workspace_id,
            session_id=policy.session_id,
            turn_id=policy.turn_id,
            policy_version=policy.policy_version,
        )
        if transaction is None:
            return None
        for target in plan.effects.write_paths:
            transaction.record_write(target, Operation.OVERWRITE)
        for target in plan.effects.delete_paths:
            transaction.record_write(target, Operation.DELETE)
        for pair in plan.effects.move_pairs:
            transaction.record_write(pair.source, Operation.MOVE)
            transaction.record_write(pair.target, Operation.REPLACE)
        return transaction

    def finish(
        self,
        transaction: MutationTransaction | None,
        plan: ToolPlan,
        result: ToolResult,
    ) -> None:
        if transaction is None:
            return
        if result.workspace_mutated is False:
            transaction.complete(failed=True)
            return
        effects = plan.effects
        for target in effects.write_paths:
            transaction.record_result(target)
        for target in effects.delete_paths:
            transaction.record_result(target, deleted=True)
        for pair in effects.move_pairs:
            transaction.record_result(pair.source, deleted=True)
            transaction.record_result(pair.target)
        transaction.complete(failed=result.status is not ToolResultStatus.OK)

    def abort(self, transaction: MutationTransaction | None) -> None:
        """执行入口驳回, 进程没起来: 事务按失败收尾, 不留一个悬挂的 ARMED 检查点."""
        if transaction is not None:
            transaction.complete(failed=True)
