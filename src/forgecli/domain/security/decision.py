"""AuthorizationDecision: 规则引擎的裁决产物 (ADR-0013 §4, ADR-0028 规则 B).

裁决不是授权. ALLOW 只表示"规则层面可以做", 之后还要经过恢复层建立可恢复性, 再由
ToolAuthorizationService.issue 签发一次性 ExecutionAuthorization. ASK 更不是授权: 它
只是创建一个待人类决定的请求.

effective_plan 允许与原计划不同, 但只能收缩; 校验在
domain.tool.authorization.validate_narrowing. 分析器把 shell.run 的 UNKNOWN 目标冻结成
FORGE_EXPANDED 就走这条通道.

**分析事实只持有引用, 不逐字段抄写.** 早先这里有 executable_identity_hash,
executable_names, script_snapshots 与 risk_facts 四个字段, 而 PolicyEngine.decide 的
六个返回分支各自抄一遍 —— 十八处赋值, 全部形如 `x=findings.x`. 抄写本身不出错, 出错的
是下一个分支: 新增一条裁决路径时漏抄一项, 审批界面就少一段信息, 而类型检查不会报错.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.security.findings import AnalysisFindings, RiskFact
from forgecli.domain.security.script_facts import ScriptSnapshot
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.plan import ToolPlan

__all__ = ["AuthorizationDecision"]


@dataclass(frozen=True)
class AuthorizationDecision:
    decision: Decision
    reason: DecisionReason
    findings: AnalysisFindings
    message: str = ""
    matched_rule_id: str | None = None
    # MANDATORY_ASK 不能被缓存, 历史批准, 学习规则或 always 满足 (ADR-0013 §4.1).
    mandatory: bool = False
    # 人类批准时看到的那份完整视图的哈希 (走过 ASK 且重验通过时才有). 它进
    # ExecutionAuthorization, 让"批准的对象"与"执行的对象"之间留下一条可核对的链.
    approval_view_hash: str | None = None

    def __post_init__(self) -> None:
        if self.mandatory and self.decision is not Decision.ASK:
            raise ValueError("mandatory 只对 ASK 有意义")

    @property
    def effective_plan(self) -> ToolPlan:
        """分析收缩之后的计划. 裁决与执行绑定的都是它, 不是工具最初给出的那份."""
        return self.findings.plan

    @property
    def risk_facts(self) -> tuple[RiskFact, ...]:
        return self.findings.risk_facts

    @property
    def executable_identity_hash(self) -> str:
        return self.findings.executable_identity_hash

    @property
    def executable_names(self) -> tuple[str, ...]:
        """可执行文件基名, 只用于展示 (审批界面与 /rules). 不参与匹配."""
        return self.findings.executable_names

    @property
    def script_snapshots(self) -> tuple[ScriptSnapshot, ...]:
        """分析实际绑定的脚本正文. 审批界面靠它展示"要跑的到底是哪段代码", 学习规则靠
        它的哈希绑内容 —— 少了它, `bash deploy.sh` 的规则只绑住命令行, 脚本改成什么都
        照样命中.
        """
        return self.findings.script_snapshots

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
        }
