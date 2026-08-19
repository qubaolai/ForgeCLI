"""本地策略引擎: 把分析事实变成裁决 (ADR-0013 §4).

求值顺序就是安全优先级, 一步都不能调换:

    Hard Deny > Mandatory Ask > 模式预算 > 分析器要求的 Ask > Allow

最后一条分支是 fail closed: 没有任何依据能证明这次调用落在模式预算内时, 结果是 DENY
而不是"没有规则匹配所以放行". 这条默认值是整套机制里最容易被写反的一行.
"""

from __future__ import annotations

from forgecli.application.security.analyzers.registry import AnalysisFindings
from forgecli.domain.intents import SessionMode
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

# 只读快速路径能免掉的能力 (ADR-0024).
#
# **只有这两项**, 这是这条路径全部的作用域. 分析已经证明这条命令等价于一次读取, 那么
# "它是经 Shell 跑的"这件事本身不该再要一次人类确认 —— 同样一次目录列举走 fs.list_files
# 本来就自动放行.
#
# 免不掉的东西同样要紧: EXTERNAL_READ 越界读取仍然超预算, 于是 ADR-0024 条件 7 (目标全
# 在工作区内) 不需要单独实现 —— 越界的读取会自己留在 over_budget 里. 把这条写成"免掉
# 全部 over_budget"会顺带放行 `cat ~/.ssh/id_rsa`.
_READ_ONLY_SHELL_CAPABILITIES = frozenset(
    {
        Capability.EXECUTE_SHELL,
        Capability.SPAWN_PROCESS,
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
        if over_budget and _proven_read_only(findings, context.mode):
            over_budget = over_budget - _READ_ONLY_SHELL_CAPABILITIES
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
            reason=_allow_reason(
                plan.capabilities,
                proven_read_only=_proven_read_only(findings, context.mode),
            ),
            effective_plan=plan,
            executable_identity_hash=findings.executable_identity_hash,
            executable_names=findings.executable_names,
            script_snapshots=findings.script_snapshots,
            risk_facts=findings.risk_facts,
        )


def _proven_read_only(findings: AnalysisFindings, mode: SessionMode) -> bool:
    """这次调用能不能走只读快速路径 (ADR-0024).

    三个条件缺一不可:

    - 分析器证明命令结构只读 (条件 1-5, 8). 只有它拿得到 CommandPlan.
    - 目标集合已封闭 (条件 6). 策略层从 ToolPlan 就能看到, 因此在这里独立再验一次 ——
      分析器漏标时这一条仍然拦得住.
    - 不是 plan 档. 那一档对用户的承诺是"不执行", 而起一个子进程就是执行 —— 它会占用
      时间, 会读东西, 也会挂住. `shell.run` 本来就不在 plan 档的工具目录里, 所以这条
      判断买不到新功能, 只是不让策略层依赖目录过滤兜底.

    条件 7 (目标全在工作区内) 由 _READ_ONLY_SHELL_CAPABILITIES 的窄作用域自动保证.
    """
    if mode is SessionMode.PLAN:
        return False
    return findings.proven_read_only and findings.plan.target_resolution.closed


def _allow_reason(
    capabilities: frozenset[Capability], *, proven_read_only: bool = False
) -> DecisionReason:
    if capabilities <= {Capability.PLAN_ONLY}:
        return DecisionReason.PLAN_ONLY_FAST_PATH
    if capabilities <= {Capability.WORKSPACE_READ, Capability.SPAWN_PROCESS}:
        return DecisionReason.WORKSPACE_READ_FAST_PATH
    if proven_read_only:
        # 与上一条分开, 审计才答得出"这次为什么没问人": 一个是工具自己就窄, 一个是这条
        # 命令被证明窄. 前者换个参数还是窄的, 后者换个参数可能就不是了.
        return DecisionReason.PROVEN_READ_ONLY_SHELL
    return DecisionReason.RULE_ALLOW


def _capability_fact(capability: Capability) -> RiskFact:
    return RiskFact(code="capability", detail=capability.value)


def _name(capability: Capability) -> str:
    return capability.value
