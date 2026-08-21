"""本地策略引擎: 把分析事实变成裁决 (ADR-0013 §4).

求值顺序就是安全优先级, 一步都不能调换:

    Hard Deny > Mandatory Ask > 模式预算 > 分析器要求的 Ask > Allow

最后一条分支是 fail closed: 没有任何依据能证明这次调用落在模式预算内时, 结果是 DENY
而不是"没有规则匹配所以放行". 这条默认值是整套机制里最容易被写反的一行.

**只有一处构造 AuthorizationDecision** (ADR-0028 规则 B). 判定逻辑写成 `_verdict`,
它只回答"哪一档, 什么理由, 附加哪几条风险事实"; 分析事实由裁决对象自己从 findings 读.
早先每个分支各构造一次, 每次抄四个 findings 字段 —— 十八处赋值, 而新增一条分支时漏抄
一项不会报错, 只会让审批界面少一段信息.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.findings import AnalysisFindings, RiskFact
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


@dataclass(frozen=True)
class _Verdict:
    """一次判定的结论. 只有这四项因分支而异, 其余全部来自 findings."""

    decision: Decision
    reason: DecisionReason
    message: str = ""
    mandatory: bool = False
    extra_facts: tuple[RiskFact, ...] = ()


class PolicyEngine:
    """确定性裁决. 无 IO, 无 LLM: 分类器结论由分析器带进 findings, 不在这里调用."""

    def decide(
        self, findings: AnalysisFindings, context: PolicyContext
    ) -> AuthorizationDecision:
        verdict = _verdict(findings, context)
        return AuthorizationDecision(
            decision=verdict.decision,
            reason=verdict.reason,
            findings=(
                findings.with_risk(*verdict.extra_facts)
                if verdict.extra_facts
                else findings
            ),
            message=verdict.message,
            mandatory=verdict.mandatory,
        )


def _verdict(findings: AnalysisFindings, context: PolicyContext) -> _Verdict:
    """求值顺序即安全优先级. 分支顺序是 ADR-0013 §4 的决策, 不能调换."""
    plan = findings.plan

    if findings.hard_deny is not None:
        return _Verdict(
            Decision.DENY, findings.hard_deny, message="命中不可覆盖的安全底线"
        )

    if findings.unrunnable is not None:
        # 排在 Hard Deny 之后: 一条既危险又跑不了的命令, 审计里该记的是安全理由.
        # 但排在其余所有分支之前 —— 跑不了的东西不值得再走模式预算与人类审批.
        return _Verdict(
            Decision.DENY, findings.unrunnable, message="这条命令在当前环境下无法执行"
        )

    if findings.mandatory_ask:
        return _Verdict(
            Decision.ASK,
            findings.requires_ask or DecisionReason.EXTERNAL_IRREVERSIBLE_EFFECT,
            message="该操作不可逆或影响共享状态, 必须逐次批准",
            mandatory=True,
        )

    never_auto = plan.capabilities & _NEVER_AUTO
    if never_auto:
        # mandatory=True 是这一支的要点. 不置位的话它只是一次普通 ASK, 而普通 ASK
        # 会被学习规则抬成 ALLOW —— 于是"读凭证永远需要人类在场"和 ADR-0013 §4.1
        # 的逐次批准, 都会在用户点过一次 always 之后失效.
        return _Verdict(
            Decision.ASK,
            findings.requires_ask or DecisionReason.MODE_REQUIRES_APPROVAL,
            message="请求的能力在任何模式下都需要人类确认",
            mandatory=True,
            extra_facts=_capability_facts(never_auto),
        )

    over_budget = capabilities_requiring_approval(context.mode, plan.capabilities)
    if over_budget and _proven_read_only(findings, context.mode):
        over_budget = over_budget - _READ_ONLY_SHELL_CAPABILITIES
    if over_budget:
        # 理由取分析器给出的那一条 (更具体), 只在没有时才落到模式预算. 反过来写会把
        # PARSE_INCOMPLETE / CLASSIFIER_UNAVAILABLE / SCRIPT_EXECUTION 全部盖掉:
        # shell.run 在 plan 与 accept_edits 下必然超预算, 于是那些理由永远不会出现
        # 在审计与界面上, 而 _UNLEARNABLE_REASONS 也就永远匹配不到它们.
        return _Verdict(
            Decision.ASK,
            findings.requires_ask or DecisionReason.MODE_REQUIRES_APPROVAL,
            message=f"当前模式 {context.mode.value} 不自动允许这些能力",
            mandatory=findings.mandatory_ask,
            extra_facts=_capability_facts(over_budget),
        )

    if findings.requires_ask is not None:
        return _Verdict(
            Decision.ASK, findings.requires_ask, message="分析结果不足以自动放行"
        )

    return _Verdict(
        Decision.ALLOW,
        _allow_reason(
            plan.capabilities,
            proven_read_only=_proven_read_only(findings, context.mode),
        ),
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


def _capability_facts(capabilities: frozenset[Capability]) -> tuple[RiskFact, ...]:
    return tuple(
        RiskFact(code="capability", detail=capability.value)
        for capability in sorted(capabilities, key=_name)
    )


def _name(capability: Capability) -> str:
    return capability.value
