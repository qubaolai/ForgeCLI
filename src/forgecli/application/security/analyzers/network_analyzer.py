"""NETWORK_ACCESS 能力的目标与凭证检查 (ADR-0013 §11 / §14).

网络请求的风险不在"联不联网", 而在**联到哪里, 带着谁的凭证**. 因此这里只做两件事:
把真实远端目标提出来供审批展示, 以及在目标无法确定时要求人类确认.

审批展示可以脱敏凭证值, 但不能隐藏"凭证会被发到哪个主机"—— 那正是用户要判断的东西.
"""

from __future__ import annotations

from forgecli.application.security.analyzers.registry import (
    AnalysisFindings,
    CapabilityAnalyzer,
)
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.findings import RiskFact
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import Capability

__all__ = ["NetworkAnalyzer"]


class NetworkAnalyzer(CapabilityAnalyzer):
    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.NETWORK_ACCESS})

    def analyze(
        self,
        findings: AnalysisFindings,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        targets = findings.plan.effects.network_targets
        if not targets:
            # 声明了联网却给不出目标: 审批界面无法展示"发到哪里", 只能问人.
            return findings.asked(
                DecisionReason.UNRESOLVED_TARGET_SET,
                RiskFact(code="network_target", detail="网络目标无法确定"),
            )
        return findings.with_risk(
            *(RiskFact(code="network_target", detail=target) for target in targets)
        )
