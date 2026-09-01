"""本地策略引擎: 把分析事实变成裁决 (ADR-0013 §4).

求值顺序就是安全优先级, 一步都不能调换:

    Hard Deny > Mandatory Ask > 围栏边界 > 分析器要求的 Ask > Allow

最后一条分支是 fail closed: 没有任何依据能证明这次调用落在模式预算内时, 结果是 DENY
而不是"没有规则匹配所以放行". 这条默认值是整套机制里最容易被写反的一行.

**只有一处构造 AuthorizationDecision** (ADR-0028 规则 B). 判定逻辑写成 `_verdict`,
它只回答"哪一档, 什么理由, 附加哪几条风险事实"; 分析事实由裁决对象自己从 findings 读.
早先每个分支各构造一次, 每次抄四个 findings 字段 —— 十八处赋值, 而新增一条分支时漏抄
一项不会报错, 只会让审批界面少一段信息.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.security.budget import capabilities_requiring_approval
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.findings import AnalysisFindings, RiskFact
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.capability import Capability

__all__ = ["PolicyEngine"]

# 无论围栏如何都要人类确认的能力. 与 budget._NEVER_AUTO 同源, 这里单独留一份是因为
# 这一支要置 mandatory —— 那是 budget 表达不了的.
_NEVER_AUTO = frozenset(
    {
        Capability.CREDENTIAL_ACCESS,
        Capability.EXTERNAL_IRREVERSIBLE_EFFECT,
        Capability.UNKNOWN,
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

    unrestricted = _only_hard_deny(context)

    if findings.mandatory_ask and not unrestricted:
        return _Verdict(
            Decision.ASK,
            findings.requires_ask or DecisionReason.EXTERNAL_IRREVERSIBLE_EFFECT,
            message="该操作不可逆或影响共享状态, 必须逐次批准",
            mandatory=True,
        )

    capabilities = _reliable_capabilities(findings, context)

    never_auto = frozenset() if unrestricted else capabilities & _NEVER_AUTO
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

    outside_fence = capabilities_requiring_approval(
        capabilities, context.fence, confined=context.confined
    )
    if outside_fence:
        # 理由取分析器给出的那一条 (更具体), 只在没有时才落到围栏边界. 反过来写会把
        # PARSE_INCOMPLETE / SCRIPT_EXECUTION 这类理由全部盖掉: 无围栏时 shell_run
        # 必然超出边界, 于是那些理由永远不会出现在审计与界面上, 而 _UNLEARNABLE_REASONS
        # 也就永远匹配不到它们.
        return _Verdict(
            Decision.ASK,
            findings.requires_ask or DecisionReason.MODE_REQUIRES_APPROVAL,
            message=f"围栏兜不住这些能力 (模式 {context.mode.value})",
            mandatory=findings.mandatory_ask,
            extra_facts=_capability_facts(outside_fence),
        )

    if findings.requires_ask is not None and not unrestricted:
        return _Verdict(
            Decision.ASK, findings.requires_ask, message="分析结果不足以自动放行"
        )

    return _Verdict(Decision.ALLOW, _allow_reason(capabilities, context))


def _only_hard_deny(context: PolicyContext) -> bool:
    """这一档是不是"围栏就是全部边界" (ADR-0030 决策 4 的 2026-08-28 修订).

    两个条件缺一不可:

    - `fence.unrestricted` —— full_access 编译出来的围栏才带它;
    - `confined` —— 围栏**确实立起来了**, 来自 Provider 的行为自测而不是"装了就算".

    第二个条件是要点. full_access 的语义是"在沙箱边界内不再另设闸", 而 UNCONFINED 时
    根本没有那条边界 —— 那时候放开这些分支等于什么都不拦, 所以退回逐项确认.

    注意它免掉的是**闸**, 不是边界: Hard Deny 在这个函数之前就返回了, 围栏自己在系统
    调用那一刻照常拦截, 工作区快照照常在破坏性写入前落盘 (ADR-0015).
    """
    return context.fence is not None and context.fence.unrestricted and context.confined


# 目标集合没封闭时靠推导得出的越界能力. 它们的判据是"这个路径在不在工作区里", 而
# 路径本身就是猜的.
_DERIVED_FROM_TARGETS = frozenset(
    {
        Capability.EXTERNAL_READ,
        Capability.EXTERNAL_WRITE,
    }
)


def _reliable_capabilities(
    findings: AnalysisFindings, context: PolicyContext
) -> frozenset[Capability]:
    """把靠不住的推导结论从裁决输入里摘掉 (ADR-0030 决策 1).

    目标集合没封闭时, "这个目标在工作区之外"是从一堆猜出来的路径得来的 ——
    `find . -exec rm {} +` 里的 `{}` 会被当成一个路径, 于是推出 EXTERNAL_WRITE, 于是
    命中 _NEVER_AUTO 变成 Mandatory Ask. 这正是本 ADR 要消灭的那类误判: 判据不是
    "它真的要写工作区外", 而是"解析器把占位符看成了路径".

    **有围栏时不必猜.** 真要碰工作区外的东西, 内核在系统调用那一刻会拒绝, 命令报错,
    模型看到失败再决定要不要请求扩张. 没有围栏时这些推导仍然是唯一的依据, 照旧生效
    (ADR-0030 决策 5).

    只摘"靠目标路径推出来的"那两项. CREDENTIAL_ACCESS 与
    EXTERNAL_IRREVERSIBLE_EFFECT 来自命令语义而不是路径归属, 不受目标封闭度影响,
    照常留在裁决输入里.
    """
    capabilities = findings.plan.capabilities
    if not context.confined or findings.plan.target_resolution.closed:
        return capabilities
    return frozenset(capabilities - _DERIVED_FROM_TARGETS)


def _allow_reason(
    capabilities: frozenset[Capability], context: PolicyContext
) -> DecisionReason:
    if capabilities <= {Capability.PLAN_ONLY}:
        return DecisionReason.PLAN_ONLY_FAST_PATH
    if capabilities <= {Capability.ARTIFACT_READ}:
        return DecisionReason.ARTIFACT_READ_FAST_PATH
    if capabilities <= {Capability.MEMORY_WRITE}:
        return DecisionReason.MEMORY_WRITE_FAST_PATH
    if capabilities <= {Capability.WORKSPACE_READ, Capability.SPAWN_PROCESS}:
        return DecisionReason.WORKSPACE_READ_FAST_PATH
    if context.confined:
        # 与上一条分开, 审计才答得出"这次为什么没问人": 一个是工具自己就窄, 一个是
        # 围栏把它关住了. 前者换个参数还是窄的, 后者靠的是内核.
        return DecisionReason.FENCE_CONFINED
    return DecisionReason.RULE_ALLOW


def _capability_facts(capabilities: frozenset[Capability]) -> tuple[RiskFact, ...]:
    return tuple(
        RiskFact(code="capability", detail=capability.value)
        for capability in sorted(capabilities, key=_name)
    )


def _name(capability: Capability) -> str:
    return capability.value
