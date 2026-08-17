"""工具系统的契约层 (ADR-0004).

这是工具机制与安全策略之间**唯一**的共同依赖: 两侧都只 import 这里, 互不 import
对方的实现. 契约包含能力闭集词汇, ToolSpec 上界, ToolPlan 事实, 授权信封, 结果归一化
和目录视图.
"""

from forgecli.domain.tool.authorization import (
    AuthorizationError,
    AuthorizationErrorCode,
    ExecutionAuthorization,
    RevocationState,
    validate_narrowing,
)
from forgecli.domain.tool.capability import (
    CAPABILITY_VOCABULARY_VERSION,
    MUTATING_CAPABILITIES,
    Capability,
    normalize_capability,
)
from forgecli.domain.tool.catalog import CatalogQuery, ToolCatalog
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.hashing import canonical, digest, digest_text
from forgecli.domain.tool.plan import (
    AnalysisSubject,
    DeclarationConfidence,
    ExecutionContextRef,
    MovePair,
    PlanEffects,
    ShellSubject,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
    empty_input,
)
from forgecli.domain.tool.result import (
    ArtifactRef,
    ContentPart,
    ToolError,
    ToolMetrics,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import (
    ArtifactPolicy,
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.domain.tool.tool_call import ToolCall, ToolSchema

__all__ = [
    "CAPABILITY_VOCABULARY_VERSION",
    "MUTATING_CAPABILITIES",
    "AnalysisSubject",
    "ArtifactPolicy",
    "ArtifactRef",
    "AuthorizationError",
    "AuthorizationErrorCode",
    "Capability",
    "CatalogQuery",
    "ContentPart",
    "DeclarationConfidence",
    "ExecutionAuthorization",
    "ExecutionContextRef",
    "MovePair",
    "PlanEffects",
    "PreparationError",
    "PreparationErrorCode",
    "RevocationState",
    "ShellSubject",
    "TargetDeclarationAbility",
    "TargetResolution",
    "ToolCall",
    "ToolCatalog",
    "ToolError",
    "ToolMetrics",
    "ToolPlan",
    "ToolResult",
    "ToolResultStatus",
    "ToolSchema",
    "ToolSpec",
    "WorkspaceScope",
    "canonical",
    "digest",
    "digest_text",
    "empty_input",
    "normalize_capability",
    "validate_narrowing",
]
