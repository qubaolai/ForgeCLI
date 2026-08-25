"""按能力分派的分析器注册表 (ADR-0004 §5).

这是工具与安全解耦的落点: 注册表的键是 **Capability**, 不是工具名. 任何工具只要声明了
EXECUTE_SHELL, 就会进同一套 Shell 分析路径; 新增工具不必改安全模块, 新增规则不必改
工具实现.

分析器链式改写同一份 AnalysisFindings: 后一个分析器看得到前一个的结论 (例如 Shell 分析
器把 UNKNOWN 目标冻结成 FORGE_EXPANDED 之后, 工作区分析器才能对具体路径做保护检查).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.findings import AnalysisFindings
from forgecli.domain.security.scripts import ScriptSnapshot
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
    事实, 输出是判断. 先 CHECK 后 DERIVE 会让检查器对着 shell_run 那个"什么都可能"的
    最宽上界报警 —— 每条 `ls` 都会被问一遍"网络目标是什么", 而它根本不联网.
    """

    DERIVE = "derive"
    CHECK = "check"


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

        context 是不可变的执行上下文: 展开 glob, 解析可执行文件和读脚本内容都必须
        用它。文件系统会继续变化，因此读过的文件还必须进入 ToolPlan 状态绑定。
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
