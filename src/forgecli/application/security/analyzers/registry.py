"""按能力分派的分析器注册表 (ADR-0004 §5).

这是工具与安全解耦的落点: 注册表的键是 **Capability**, 不是工具名. 任何工具只要声明了
EXECUTE_SHELL, 就会进同一套 Shell 分析路径; 新增工具不必改安全模块, 新增规则不必改
工具实现.

分析器链式改写同一份 AnalysisFindings: 后一个分析器看得到前一个的结论 (例如 Shell 分析
器把 UNKNOWN 目标冻结成 FORGE_EXPANDED 之后, 工作区分析器才能对具体路径做保护检查).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum

from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import RiskFact
from forgecli.domain.security.script_facts import ScriptSnapshot
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import ToolPlan

__all__ = [
    "AnalysisFindings",
    "ScriptSnapshot",
    "AnalyzerStage",
    "CapabilityAnalyzer",
    "CapabilityAnalyzerRegistry",
]


class AnalyzerStage(Enum):
    """分析器分两轮跑, 顺序不能反.

    DERIVE 的输入是不透明材料 (一条命令串), 输出是事实; CHECK 的输入是**已经确定的**
    事实, 输出是判断. 先 CHECK 后 DERIVE 会让检查器对着 shell.run 那个"什么都可能"的
    最宽上界报警 —— 每条 `ls` 都会被问一遍"网络目标是什么", 而它根本不联网.
    """

    DERIVE = "derive"
    CHECK = "check"


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

    def with_scripts(self, *snapshots: ScriptSnapshot) -> AnalysisFindings:
        return replace(self, script_snapshots=(*self.script_snapshots, *snapshots))

    def with_plan(self, plan: ToolPlan) -> AnalysisFindings:
        return replace(self, plan=plan)

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


class CapabilityAnalyzer(ABC):
    """一类能力的分析器. 只看 ToolPlan 与策略上下文, 不认识任何具体工具类型."""

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[Capability]:
        """本分析器负责的能力."""

    @property
    def stage(self) -> AnalyzerStage:
        """默认是检查器. 能从原始材料推导事实的分析器要覆盖成 DERIVE."""
        return AnalyzerStage.CHECK

    @abstractmethod
    def analyze(
        self,
        findings: AnalysisFindings,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        """在已有结论上继续分析. 只能收紧, 不能放宽.

        context 是**已冻结**的执行上下文: 展开 glob, 解析可执行文件和读脚本内容都必须
        用它, 与 prepare 和真实执行是同一份视图, 否则分析的和执行的就不是一回事.
        """


class CapabilityAnalyzerRegistry:
    """能力 -> 分析器. 同一分析器可以登记多个能力, 一次调用内只跑一遍."""

    def __init__(self) -> None:
        self._by_capability: dict[Capability, list[CapabilityAnalyzer]] = {}

    def register(self, analyzer: CapabilityAnalyzer) -> None:
        for capability in analyzer.capabilities:
            self._by_capability.setdefault(capability, []).append(analyzer)

    def register_all(self, analyzers: tuple[CapabilityAnalyzer, ...]) -> None:
        for analyzer in analyzers:
            self.register(analyzer)

    def analyze(
        self,
        plan: ToolPlan,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        findings = AnalysisFindings(plan=plan, declared_capabilities=plan.capabilities)
        # 第一轮按**声明的**能力选推导器: 计划这时还是不透明的, 只能照上界派活.
        for analyzer in self._ordered(plan.capabilities, AnalyzerStage.DERIVE):
            findings = analyzer.analyze(findings, policy, context)
        # 第二轮按**收缩后的**能力选检查器: 事实已经确定, 该查什么一目了然.
        for analyzer in self._ordered(findings.plan.capabilities, AnalyzerStage.CHECK):
            findings = analyzer.analyze(findings, policy, context)
        return findings

    def _ordered(
        self, capabilities: frozenset[Capability], stage: AnalyzerStage
    ) -> tuple[CapabilityAnalyzer, ...]:
        """按能力枚举声明序取分析器并去重, 保证同一组能力每次都以同一顺序分析."""
        ordered: list[CapabilityAnalyzer] = []
        for capability in Capability:
            if capability not in capabilities:
                continue
            for analyzer in self._by_capability.get(capability, ()):
                if analyzer.stage is stage and analyzer not in ordered:
                    ordered.append(analyzer)
        return tuple(ordered)
