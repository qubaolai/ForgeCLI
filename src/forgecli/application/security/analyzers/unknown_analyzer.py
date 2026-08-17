"""UNKNOWN 能力的兜底路径 (ADR-0013 §4).

工具声明了当前词汇表识别不了的能力, 或者 MCP adapter 无法从 schema 证明能力时, 计划里
就会出现 UNKNOWN. 它的处理原则只有一条: **无法确定不等于放行**.

这里做的是"交给人类"这一步. 风险分类器接入后 (ADR-0013 §10), UNKNOWN 会先经分类器给出
风险事实, 再由本地策略引擎裁决; 但分类器失败, 超时或低置信度时, 结论仍然回到这里 ——
阻塞式 ASK.
"""

from __future__ import annotations

from forgecli.application.security.analyzers.registry import (
    AnalysisFindings,
    CapabilityAnalyzer,
)
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import RiskFact
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import Capability

__all__ = ["UnknownCapabilityAnalyzer"]


class UnknownCapabilityAnalyzer(CapabilityAnalyzer):
    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.UNKNOWN})

    def analyze(
        self,
        findings: AnalysisFindings,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        return findings.asked(
            DecisionReason.UNEVALUATED_CAPABILITY,
            RiskFact(
                code="unknown_capability",
                detail=(
                    f"{findings.plan.tool_name} 声明了无法识别的能力, "
                    "本次调用的影响范围无法证明"
                ),
            ),
        )
