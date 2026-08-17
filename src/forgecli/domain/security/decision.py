"""AuthorizationDecision: 规则引擎的裁决产物 (ADR-0013 §4).

裁决不是授权. ALLOW 只表示"规则层面可以做", 之后还要经过恢复层建立可恢复性, 再由
ToolAuthorizationService.issue 签发一次性 ExecutionAuthorization. ASK 更不是授权: 它
只是创建一个待人类决定的请求.

effective_plan 允许与原计划不同, 但只能收缩; 校验在
domain.tool.authorization.validate_narrowing. 分析器把 shell.run 的 UNKNOWN 目标冻结成
FORGE_EXPANDED 就走这条通道.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forgecli.domain.security.script_facts import ScriptSnapshot
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.plan import ToolPlan

__all__ = ["AuthorizationDecision", "RiskFact"]


@dataclass(frozen=True)
class RiskFact:
    """触发本次裁决的一条具体风险事实, 直接进审批展示与审计.

    detail 面向人类阅读, 不做语义截断: 审批界面可以折叠, 但不能只显示前若干项.
    """

    code: str
    detail: str


@dataclass(frozen=True)
class AuthorizationDecision:
    decision: Decision
    reason: DecisionReason
    effective_plan: ToolPlan
    message: str = ""
    matched_rule_id: str | None = None
    risk_facts: tuple[RiskFact, ...] = ()
    # MANDATORY_ASK 不能被缓存, 历史批准, 学习规则或 always 满足 (ADR-0013 §4.1).
    mandatory: bool = False
    can_retry: bool = False
    executable_identity_hash: str = ""
    # 可执行文件基名, 只用于展示 (审批界面与 /rules). 不参与匹配, 也不进 audit payload
    # 的裁决字段 —— 它是给人认的标签, 不是裁决依据.
    executable_names: tuple[str, ...] = ()
    # 分析实际绑定的脚本正文. 审批界面靠它展示"要跑的到底是哪段代码", 学习规则靠它的
    # 哈希绑内容 —— 少了它, `bash deploy.sh` 的规则只绑住命令行, 脚本改成什么都照样命中.
    script_snapshots: tuple[ScriptSnapshot, ...] = ()
    # 人类批准时看到的那份完整视图的哈希 (走过 ASK 且重验通过时才有). 它进
    # ExecutionAuthorization, 让"批准的对象"与"执行的对象"之间留下一条可核对的链.
    approval_presentation_hash: str | None = None
    audit_extras: tuple[tuple[str, str], ...] = field(default=())

    def __post_init__(self) -> None:
        if self.mandatory and self.decision is not Decision.ASK:
            raise ValueError("mandatory 只对 ASK 有意义")

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    def to_audit_payload(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reason": self.reason.value,
            "plan_hash": self.effective_plan.plan_hash,
            "tool_name": self.effective_plan.tool_name,
            "matched_rule_id": self.matched_rule_id,
            "mandatory": self.mandatory,
            "risk_facts": [fact.code for fact in self.risk_facts],
            **dict(self.audit_extras),
        }
