"""本地策略引擎: 把分析事实变成裁决 (ADR-0013 §4).

求值顺序就是安全优先级, 一步都不能调换:

    Hard Deny > Mandatory Ask > 模式预算 > 分析器要求的 Ask > Allow

最后一条分支是 fail closed: 没有任何依据能证明这次调用落在模式预算内时, 结果是 DENY
而不是"没有规则匹配所以放行". 这条默认值是整套机制里最容易被写反的一行.
"""

from __future__ import annotations

from forgecli.application.security.analyzers.registry import AnalysisFindings
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision, RiskFact
from forgecli.domain.security.modes import capabilities_requiring_approval
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.capability import Capability

__all__ = ["PolicyEngine"]

# 无论哪种模式都不能自动放行, 且不接受"模式预算内"这条理由的能力.
_NEVER_AUTO = frozenset(
    {
        Capability.CREDENTIAL_ACCESS,
        Capability.EXTERNAL_IRREVERSIBLE_EFFECT,
        Capability.UNKNOWN,
    }
)


class PolicyEngine:
    """确定性裁决. 无 IO, 无 LLM: 分类器结论由分析器带进 findings, 不在这里调用."""

    def decide(
        self, findings: AnalysisFindings, context: PolicyContext
    ) -> AuthorizationDecision:
        plan = findings.plan

        if findings.hard_deny is not None:
            return AuthorizationDecision(
                decision=Decision.DENY,
                reason=findings.hard_deny,
                effective_plan=plan,
                executable_identity_hash=findings.executable_identity_hash,
                executable_names=findings.executable_names,
                script_snapshots=findings.script_snapshots,
                message="命中不可覆盖的安全底线",
                risk_facts=findings.risk_facts,
            )

        if findings.unrunnable is not None:
            # 排在 Hard Deny 之后: 一条既危险又跑不了的命令, 审计里该记的是安全理由.
            # 但排在其余所有分支之前 —— 跑不了的东西不值得再走模式预算与人类审批.
            return AuthorizationDecision(
                decision=Decision.DENY,
                reason=findings.unrunnable,
                effective_plan=plan,
                executable_identity_hash=findings.executable_identity_hash,
                executable_names=findings.executable_names,
                script_snapshots=findings.script_snapshots,
                message="这条命令在当前环境下无法执行",
                risk_facts=findings.risk_facts,
            )

        if findings.mandatory_ask:
            return AuthorizationDecision(
                decision=Decision.ASK,
                reason=findings.requires_ask
                or DecisionReason.EXTERNAL_IRREVERSIBLE_EFFECT,
                effective_plan=plan,
                executable_identity_hash=findings.executable_identity_hash,
                executable_names=findings.executable_names,
                script_snapshots=findings.script_snapshots,
                message="该操作不可逆或影响共享状态, 必须逐次批准",
                risk_facts=findings.risk_facts,
                mandatory=True,
            )

        never_auto = plan.capabilities & _NEVER_AUTO
        if never_auto:
            # mandatory=True 是这一支的要点. 不置位的话它只是一次普通 ASK, 而普通 ASK
            # 会被学习规则抬成 ALLOW —— 于是"读凭证永远需要人类在场"和 ADR-0013 §4.1
            # 的逐次批准, 都会在用户点过一次 always 之后失效.
            return AuthorizationDecision(
                decision=Decision.ASK,
                reason=findings.requires_ask or DecisionReason.MODE_REQUIRES_APPROVAL,
                effective_plan=plan,
                executable_identity_hash=findings.executable_identity_hash,
                executable_names=findings.executable_names,
                script_snapshots=findings.script_snapshots,
                message="请求的能力在任何模式下都需要人类确认",
                risk_facts=(
                    *findings.risk_facts,
                    *(_capability_fact(cap) for cap in sorted(never_auto, key=_name)),
                ),
                mandatory=True,
            )

        over_budget = capabilities_requiring_approval(context.mode, plan.capabilities)
        if over_budget:
            # 理由取分析器给出的那一条 (更具体), 只在没有时才落到模式预算. 反过来写会把
            # PARSE_INCOMPLETE / CLASSIFIER_UNAVAILABLE / SCRIPT_EXECUTION 全部盖掉:
            # shell.run 在 plan 与 accept_edits 下必然超预算, 于是那些理由永远不会出现
            # 在审计与界面上, 而 _UNLEARNABLE_REASONS 也就永远匹配不到它们.
            return AuthorizationDecision(
                decision=Decision.ASK,
                reason=findings.requires_ask or DecisionReason.MODE_REQUIRES_APPROVAL,
                effective_plan=plan,
                executable_identity_hash=findings.executable_identity_hash,
                executable_names=findings.executable_names,
                script_snapshots=findings.script_snapshots,
                message=f"当前模式 {context.mode.value} 不自动允许这些能力",
                risk_facts=(
                    *findings.risk_facts,
                    *(_capability_fact(cap) for cap in sorted(over_budget, key=_name)),
                ),
                mandatory=findings.mandatory_ask,
            )

        if findings.requires_ask is not None:
            return AuthorizationDecision(
                decision=Decision.ASK,
                reason=findings.requires_ask,
                effective_plan=plan,
                executable_identity_hash=findings.executable_identity_hash,
                executable_names=findings.executable_names,
                script_snapshots=findings.script_snapshots,
                message="分析结果不足以自动放行",
                risk_facts=findings.risk_facts,
            )

        return AuthorizationDecision(
            decision=Decision.ALLOW,
            reason=_allow_reason(plan.capabilities),
            effective_plan=plan,
            executable_identity_hash=findings.executable_identity_hash,
            executable_names=findings.executable_names,
            script_snapshots=findings.script_snapshots,
            risk_facts=findings.risk_facts,
        )


def _allow_reason(capabilities: frozenset[Capability]) -> DecisionReason:
    if capabilities <= {Capability.PLAN_ONLY}:
        return DecisionReason.PLAN_ONLY_FAST_PATH
    if capabilities <= {Capability.WORKSPACE_READ, Capability.SPAWN_PROCESS}:
        return DecisionReason.WORKSPACE_READ_FAST_PATH
    return DecisionReason.RULE_ALLOW


def _capability_fact(capability: Capability) -> RiskFact:
    return RiskFact(code="capability", detail=capability.value)


def _name(capability: Capability) -> str:
    return capability.value
