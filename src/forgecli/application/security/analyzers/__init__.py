"""按能力分派的分析器 (ADR-0004 §5).

每个分析器只负责一类能力: EXECUTE_SHELL 交给 Shell 分析器, EXECUTE_SCRIPT 交给
ScriptAnalyzer, WORKSPACE_* 交给路径与受保护路径检查, UNKNOWN 走 Hard Deny 预扫描加
风险分类器. 分派的键是能力, 不是工具名.
"""

from forgecli.application.security.analyzers.registry import (
    AnalysisFindings,
    CapabilityAnalyzer,
    CapabilityAnalyzerRegistry,
)

__all__ = ["AnalysisFindings", "CapabilityAnalyzer", "CapabilityAnalyzerRegistry"]
