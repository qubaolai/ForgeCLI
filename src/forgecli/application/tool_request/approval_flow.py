"""审批视图与绑定的构造 (ADR-0013 §14, ADR-0028 规则 B / C).

这里全部是纯函数: 从裁决结果与策略上下文拼出人要看的那份视图, 以及批准后用来重验的
那份绑定. 阻塞等待与重验编排留在 ToolRequestCoordinator —— 那是流程, 这里是内容.

**视图不抄 plan 里已有的东西.** 路径, 目标封闭度, cwd 与写入内容由 ApprovalView 从
plan 现读; 这里只补 plan 里没有的三类事实: 人类语境 (用户这句话想干什么, 工作区在哪),
分析结论 (脚本正文, 风险事实, 未封闭原因), 界面提供的选项.
"""

from __future__ import annotations

from forgecli.application.security.learned_rules import LearnedRuleService
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.approval import ApprovalBinding, ApprovalView
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.plan import ToolPlan

__all__ = ["build_binding", "build_view", "scopes_for", "unresolved_reason_of"]

# 分析器给出的这几条风险事实是在谈"目标为什么说不清". 界面不能只说"未封闭"就完事 ——
# 用户要判断的是"哪一部分说不清", 而那句话已经由分析器写好了.
_UNRESOLVED_CODES = frozenset(
    {"target_resolution", "unproven_executable", "unresolved_target"}
)


def scopes_for(
    decision: AuthorizationDecision, learned: LearnedRuleService | None
) -> tuple[ApprovalScope, ...]:
    """界面该提供哪几个授权范围.

    只有可沉淀成规则的请求才给用户 always 这个选项. 给了却学不到, 用户会以为自己授权过,
    下次再被问时只会觉得系统坏了.
    """
    if learned is not None and learned.learnable(decision):
        return (ApprovalScope.ONCE, ApprovalScope.WORKSPACE)
    return (ApprovalScope.ONCE,)


def learn_block_reason(
    decision: AuthorizationDecision, learned: LearnedRuleService | None
) -> str:
    """界面上"始终允许"为什么不可选. 可选时返回空串.

    与 scopes_for 成对: 那个决定按钮在不在, 这个决定旁边写什么. 少了这一句, 按钮就只是
    静默消失, 用户看到的是"有时候有, 有时候没有"。
    """
    if learned is None:
        return "本次运行没有启用学习规则"
    return learned.block_reason(decision)


def unresolved_reason_of(decision: AuthorizationDecision) -> str | None:
    """目标集合为什么没封闭. 已封闭时返回 None."""
    if decision.effective_plan.target_resolution.closed:
        return None
    reasons = tuple(
        fact.detail for fact in decision.risk_facts if fact.code in _UNRESOLVED_CODES
    )
    return "; ".join(reasons) if reasons else "分析无法确定完整目标集合"


def build_view(
    decision: AuthorizationDecision,
    policy: PolicyContext,
    context: ExecutionContext,
    *,
    allowed_scopes: tuple[ApprovalScope, ...],
    learn_blocked_reason: str = "",
) -> ApprovalView:
    plan = decision.effective_plan
    summary = decision.message or decision.reason.value
    return ApprovalView(
        plan=plan,
        action_summary=f"{plan.tool_name}: {summary}",
        mode=policy.mode.value,
        user_intent_summary=policy.user_intent_summary,
        # 工作区目录进视图: 用户要看的第一件事是"这次动作能碰到哪些根目录".
        workspace_roots=context.workspace_roots,
        # 正文取**安全分析实际绑定的那几份快照**, 不是路径, 也不重新读一次文件
        # (重新读就是又开一个 TOCTOU 窗口).
        script_snapshots=decision.script_snapshots,
        risk_facts=tuple(fact.detail for fact in decision.risk_facts),
        unresolved_reason=unresolved_reason_of(decision),
        allowed_scopes=allowed_scopes,
        learn_blocked_reason=learn_blocked_reason,
    )


def build_binding(
    plan: ToolPlan,
    catalog: ToolCatalog,
    policy: PolicyContext,
    view_hash: str,
) -> ApprovalBinding:
    return ApprovalBinding.of(
        plan,
        catalog=catalog,
        execution_profile_hash=policy.execution_profile_hash,
        policy_version=policy.policy_version,
        mode=policy.mode,
        view_hash=view_hash,
    )
