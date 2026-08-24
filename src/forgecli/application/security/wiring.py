"""安全模块的标准装配 (ADR-0004 §5).

一个地方决定"哪些分析器参与分析". 之所以值得单独一个模块: 漏注册一个分析器的后果是
静默的 —— 相关能力不会报错, 只会没人检查. 集中在这里, 至少漏了能一眼看出来.
"""

from __future__ import annotations

from forgecli.application.security.analyzers.network_analyzer import NetworkAnalyzer
from forgecli.application.security.analyzers.registry import CapabilityAnalyzerRegistry
from forgecli.application.security.analyzers.script_binding import (
    ScriptBindingAnalyzer,
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
from forgecli.application.security.executable_resolver import ExecutableResolver
from forgecli.domain.security.protected_paths import ProtectedPathPolicy

__all__ = ["build_analyzer_registry"]


def build_analyzer_registry(
    protected_paths: ProtectedPathPolicy,
    *,
    resolver: ExecutableResolver | None = None,
) -> CapabilityAnalyzerRegistry:
    """装配全部分析器.

    ADR-0030 之后这里没有 LLM 分类器了: 它的唯一调用点是脚本正文的风险分析, 而那一层
    随围栏落地整体删除 (ADR-0020 因此转 Superseded). 剩下的五个分析器只产出事实 ——
    命令结构, 脚本正文快照, 路径归属, 网络目标, 未知能力 —— 裁决由围栏边界决定.
    """
    registry = CapabilityAnalyzerRegistry()
    registry.register_all(
        (
            ShellCapabilityAnalyzer(resolver or ExecutableResolver()),
            ScriptBindingAnalyzer(),
            WorkspacePathAnalyzer(protected_paths),
            NetworkAnalyzer(),
            UnknownCapabilityAnalyzer(),
        )
    )
    return registry
