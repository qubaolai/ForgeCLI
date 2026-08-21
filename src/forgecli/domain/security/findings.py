"""AnalysisFindings: 能力分析器链的累积结论 (ADR-0013 §2, ADR-0028 规则 B).

它是**纯值对象** —— 只有 ToolPlan, RiskFact, ScriptSnapshot, CommandPlan 与词汇枚举,
没有 IO, 也不认识 ExecutionContext. 因此它属于 domain 而不是 application: 分析器本身
要读盘要调分类器, 留在 application; 分析**得到的事实**留在这里, 让 AuthorizationDecision
能直接持有它, 而不必把每一项再抄一遍 (ADR-0028 规则 B).

plan 可能被逐步收缩, 但永远不会被放大 —— validate_narrowing 在裁决前校验这一点.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from forgecli.domain.security.script_facts import ScriptSnapshot
from forgecli.domain.security.shell.command_plan import CommandPlan
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import ToolPlan

__all__ = ["AnalysisFindings", "RiskFact"]


@dataclass(frozen=True)
class RiskFact:
    """触发本次裁决的一条具体风险事实, 直接进审批展示与审计.

    detail 面向人类阅读, 不做语义截断: 审批界面可以折叠, 但不能只显示前若干项.
    """

    code: str
    detail: str


@dataclass(frozen=True)
class AnalysisFindings:
    """分析器链的累积结论. plan 可能被逐步收缩, 但永远不会被放大."""

    plan: ToolPlan
    # 工具声明的能力上界 (进来时那一份, 不随收缩变化). 分析器**恢复**一个先前被收缩掉
    # 的能力时要拿它当界: 拿收缩后的 plan.capabilities 当界会得到一个恒等交集, 于是
    # 后一个分析器新发现的事实会被静默丢掉.
    declared_capabilities: frozenset[Capability] = frozenset()
    risk_facts: tuple[RiskFact, ...] = ()
    hard_deny: DecisionReason | None = None
    # 这次调用在这台机器上**根本跑不起来** (例如 Windows 上的 `ls`). 与 hard_deny 分开:
    # 那是"不许跑", 这是"跑不了" —— 前者要进安全审计, 后者只该让模型换个命令.
    #
    # 结果是 DENY 而不是 ASK, 理由是**批准买不到任何东西**: 解析用的受控 PATH 与执行用的
    # 是同一份, 人点了同意它照样失败. 一个批准了也没用的请求不该占用一次人类打断.
    unrunnable: DecisionReason | None = None
    requires_ask: DecisionReason | None = None
    mandatory_ask: bool = False
    # 分析证明这次调用等价于一次读取 (ADR-0024). 只有 Shell 分析器会填, 因为只有它拿得到
    # CommandPlan —— 策略层看不见单元, 连接符与重定向.
    #
    # 它**只免掉模式预算里的 EXECUTE_SHELL / SPAWN_PROCESS 这一项**, 不免任何别的:
    # requires_ask, Hard Deny, 受保护路径, 以及 EXTERNAL_READ 一类越界能力照常裁决.
    # 写成一个通用的"放行"标志会让下一个人以为它能盖掉更多.
    proven_read_only: bool = False
    # 本次命令用到的可执行文件身份的合并哈希. 只有 shell 分析器会填 —— 学习规则要靠它
    # 绑定"是哪个二进制", 拿不到就不允许沉淀成规则 (ADR-0013 §5.1).
    executable_identity_hash: str = ""
    # 同一批可执行文件的**基名**, 仅用于给人看 (审批界面, /rules 列表). 只放基名不放
    # 完整命令: 基名 (git, npm) 带不出凭证或主机名, 而参数会.
    executable_names: tuple[str, ...] = ()
    # 分析实际绑定的那几份脚本正文. 审批界面要展示它, 学习规则要用它的哈希 ——
    # 两者都必须拿到"分析读过的那一份", 而不是重新去读一次文件 (那就又是一个 TOCTOU).
    script_snapshots: tuple[ScriptSnapshot, ...] = ()
    # Shell 只解析一次; 脚本分析器消费同一棵 CommandPlan, 不再重读原命令.
    command_plan: CommandPlan | None = None

    def with_scripts(self, *snapshots: ScriptSnapshot) -> AnalysisFindings:
        return replace(self, script_snapshots=(*self.script_snapshots, *snapshots))

    def with_plan(self, plan: ToolPlan) -> AnalysisFindings:
        return replace(self, plan=plan)

    def with_command(self, command: CommandPlan) -> AnalysisFindings:
        return replace(self, command_plan=command)

    def with_identity(
        self, identity_hash: str, names: tuple[str, ...] = ()
    ) -> AnalysisFindings:
        return replace(
            self, executable_identity_hash=identity_hash, executable_names=names
        )

    def with_risk(self, *facts: RiskFact) -> AnalysisFindings:
        return replace(self, risk_facts=(*self.risk_facts, *facts))

    def denied(self, reason: DecisionReason, *facts: RiskFact) -> AnalysisFindings:
        """命中 Hard Deny. 先到先得: 已经有 Hard Deny 时保留最先命中的那条理由."""
        if self.hard_deny is not None:
            return self.with_risk(*facts)
        return replace(self, hard_deny=reason, risk_facts=(*self.risk_facts, *facts))

    def cannot_run(self, reason: DecisionReason, *facts: RiskFact) -> AnalysisFindings:
        """这次调用跑不起来. 同样先到先得, 保留最先发现的那条理由."""
        if self.unrunnable is not None:
            return self.with_risk(*facts)
        return replace(self, unrunnable=reason, risk_facts=(*self.risk_facts, *facts))

    def asked(
        self, reason: DecisionReason, *facts: RiskFact, mandatory: bool = False
    ) -> AnalysisFindings:
        """需要人类确认. mandatory 一旦置位就不会被后续分析器降回普通 ASK."""
        return replace(
            self,
            requires_ask=self.requires_ask or reason,
            mandatory_ask=self.mandatory_ask or mandatory,
            risk_facts=(*self.risk_facts, *facts),
        )
