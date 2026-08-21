"""WORKSPACE_* / EXTERNAL_* 能力的路径检查 (ADR-0014 §5.1).

Shell 分析器已经对命令做过一遍受保护路径检查, 但 `fs.edit_file` 这类工具**不经过
Shell**: 它们直接在 prepare 里声明目标路径. 那条路径同样要过保护策略, 否则"用 fs 工具
写 ~/.ssh/authorized_keys"就成了绕过 Shell 检查的捷径.

这个分析器按能力注册, 因此对两条路径一视同仁 —— 这正是按能力而不是按工具名分派的意义.
"""

from __future__ import annotations

from forgecli.application.security.analyzers.registry import (
    AnalysisFindings,
    CapabilityAnalyzer,
)
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.findings import RiskFact
from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import ToolPlan, WorkspaceScope

__all__ = ["WorkspacePathAnalyzer"]


class WorkspacePathAnalyzer(CapabilityAnalyzer):
    def __init__(self, protected_paths: ProtectedPathPolicy) -> None:
        self._protected = protected_paths

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.WORKSPACE_READ,
                Capability.WORKSPACE_WRITE,
                Capability.WORKSPACE_DELETE,
                Capability.PATH_MOVE,
                Capability.EXTERNAL_READ,
                Capability.EXTERNAL_WRITE,
                Capability.CREDENTIAL_ACCESS,
            }
        )

    def analyze(
        self,
        findings: AnalysisFindings,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        plan = findings.plan
        effects = plan.effects
        result = findings

        for path in effects.mutating_targets:
            root = self._protected.classify(_real(path, context))
            if root is not None:
                return result.denied(
                    DecisionReason.HARD_DENY_PROTECTED_PATH,
                    RiskFact(
                        code="protected_path",
                        detail=f"写入受保护路径 ({root.category.value}): {path}",
                    ),
                )
        for path in effects.read_paths:
            root = self._protected.classify(_real(path, context))
            if root is not None and root.deny_read:
                return result.denied(
                    DecisionReason.HARD_DENY_CREDENTIAL_ACCESS,
                    RiskFact(
                        code="protected_path",
                        detail=f"读取受保护内容 ({root.category.value}): {path}",
                    ),
                )

        # 目标集合封不封得住, 决定下面两条结论可不可信 (ADR-0030 决策 1).
        # 封不住时它们是从猜出来的路径推的, 有围栏就不该拿它去问人 —— 真越界了内核会拒.
        closed = plan.target_resolution.closed
        derived_only = policy.confined and not closed

        if plan.workspace_scope is WorkspaceScope.OUTSIDE and _touches_paths(plan):
            fact = RiskFact(code="workspace_scope", detail="目标位于工作区之外")
            result = (
                result.with_risk(fact)
                if derived_only
                else result.asked(DecisionReason.OUTSIDE_WORKSPACE, fact)
            )
        if plan.mutates_workspace and not closed:
            fact = RiskFact(
                code="target_resolution",
                detail=f"写入目标为 {plan.target_resolution.value}, 未封闭",
            )
            # 有围栏时这条只影响恢复层选 TARGETED 还是 FULL, 不影响裁决.
            result = (
                result.with_risk(fact)
                if policy.confined
                else result.asked(DecisionReason.UNRESOLVED_TARGET_SET, fact)
            )
        return result


def _touches_paths(plan: ToolPlan) -> bool:
    return bool(plan.effects.read_paths or plan.effects.mutating_targets)


def _real(path: str, context: ExecutionContext) -> str:
    """保护判定一律看 realpath: 符号链接与平台别名挡不住字符串前缀匹配."""
    facts = context.filesystem.facts(path)
    return facts.realpath or path
