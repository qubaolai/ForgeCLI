"""安全模块的标准装配 (ADR-0004 §5).

一个地方决定"哪些分析器参与分析". 之所以值得单独一个模块: 漏注册一个分析器的后果是
静默的 —— 相关能力不会报错, 只会没人检查. 集中在这里, 至少漏了能一眼看出来.
"""

from __future__ import annotations

from forgecli.application.security.analyzers.network_analyzer import NetworkAnalyzer
from forgecli.application.security.analyzers.registry import CapabilityAnalyzerRegistry
from forgecli.application.security.analyzers.script_analyzer import (
    ScriptExecutionAnalyzer,
)
from forgecli.application.security.analyzers.shell_analyzer import (
    ShellCapabilityAnalyzer,
)
from forgecli.application.security.analyzers.unknown_analyzer import (
    UnknownCapabilityAnalyzer,
)
from forgecli.application.security.analyzers.workspace_analyzer import (
    WorkspacePathAnalyzer,
)
from forgecli.application.security.classifier import (
    FailSafeClassifier,
)
from forgecli.application.security.executable_resolver import ExecutableResolver
from forgecli.application.security.risk_cache import RiskCache
from forgecli.domain.security.protected_paths import ProtectedPathPolicy

__all__ = ["build_analyzer_registry"]


def build_analyzer_registry(
    protected_paths: ProtectedPathPolicy,
    *,
    resolver: ExecutableResolver | None = None,
    classifier: FailSafeClassifier,
    cache: RiskCache | None = None,
) -> CapabilityAnalyzerRegistry:
    """装配全部分析器.

    classifier 缺省是 UnavailableSafetyClassifier: **没接分类器等于分类器不可用**,
    结论是 ASK, 不是"没发现问题所以放行". 真实部署由组合根注入
    GatewaySafetyClassifier; 需要确定的低风险结论的测试自己注入 FakeSafetyClassifier.
    """
    registry = CapabilityAnalyzerRegistry()
    registry.register_all(
        (
            ShellCapabilityAnalyzer(resolver or ExecutableResolver()),
            ScriptExecutionAnalyzer(
                classifier,
                cache=cache,
            ),
            WorkspacePathAnalyzer(protected_paths),
            NetworkAnalyzer(),
            UnknownCapabilityAnalyzer(),
        )
    )
    return registry
