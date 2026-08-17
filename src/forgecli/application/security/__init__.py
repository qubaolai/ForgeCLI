"""安全策略层 (ADR-0013): 分析, 裁决, 审批与授权签发.

这一层**不认识任何具体工具类型**: 它只消费 domain/tool 里的 ToolSpec, ToolPlan, 能力
词汇和授权信封, 按能力分派分析器. 新增工具不需要改这里, 新增规则不需要改工具.
"""

from forgecli.application.security.analyzers.registry import (
    AnalysisFindings,
    CapabilityAnalyzer,
    CapabilityAnalyzerRegistry,
)
from forgecli.application.security.approval_service import (
    ApprovalService,
    PendingApprovalService,
)
from forgecli.application.security.authorization_service import (
    AuthorizationIssueError,
    ToolAuthorizationService,
)
from forgecli.application.security.executable_resolver import ExecutableResolver
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.wiring import build_analyzer_registry
from forgecli.application.security.workspace_grants import (
    DirectoryGrant,
    GrantAccess,
    GrantError,
    WorkspaceGrants,
)

__all__ = [
    "AnalysisFindings",
    "ApprovalService",
    "AuthorizationIssueError",
    "CapabilityAnalyzer",
    "CapabilityAnalyzerRegistry",
    "DirectoryGrant",
    "ExecutableResolver",
    "GrantAccess",
    "GrantError",
    "PendingApprovalService",
    "PolicyEngine",
    "ToolAuthorizationService",
    "WorkspaceGrants",
    "build_analyzer_registry",
]
