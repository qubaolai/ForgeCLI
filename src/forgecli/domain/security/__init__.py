"""安全裁决的领域词汇 (ADR-0013).

纯值对象与纯函数: 裁决词汇, 策略上下文, 裁决结果, mode 能力矩阵, Hard Deny 底线,
学习规则, 可执行文件身份, 受保护路径, 审批展示与 Shell 解析.

有 IO 的部分 (读盘解析可执行文件, 调分类器, 存学习规则) 在 application/security.
"""

from forgecli.domain.security.approval import (
    ApprovalBinding,
    ApprovalOutcome,
    ApprovalPresentation,
    ApprovalRequest,
    ApprovalResponse,
)
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision, RiskFact
from forgecli.domain.security.executable_identity import ExecutableIdentity, TrustZone
from forgecli.domain.security.hard_deny import (
    HardDenyHit,
    inspect_command,
    prefilter_raw,
)
from forgecli.domain.security.modes import (
    auto_allowed_capabilities,
    capabilities_requiring_approval,
)
from forgecli.domain.security.protected_paths import (
    PathVerdict,
    ProtectedCategory,
    ProtectedPathPolicy,
    ProtectedRoot,
)
from forgecli.domain.security.rules import (
    LearnedAllowRule,
    RuleMatch,
    RuleSet,
    can_learn,
)
from forgecli.domain.security.vocabulary import (
    POLICY_VERSION,
    ApprovalScope,
    Decision,
    DecisionReason,
)

__all__ = [
    "POLICY_VERSION",
    "ApprovalBinding",
    "ApprovalOutcome",
    "ApprovalPresentation",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalScope",
    "AuthorizationDecision",
    "Decision",
    "DecisionReason",
    "ExecutableIdentity",
    "HardDenyHit",
    "LearnedAllowRule",
    "PathVerdict",
    "PolicyContext",
    "ProtectedCategory",
    "ProtectedPathPolicy",
    "ProtectedRoot",
    "RiskFact",
    "RuleMatch",
    "RuleSet",
    "TrustZone",
    "auto_allowed_capabilities",
    "can_learn",
    "capabilities_requiring_approval",
    "inspect_command",
    "prefilter_raw",
]
