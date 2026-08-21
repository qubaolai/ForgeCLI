"""ExecutionAuthorization: 安全模块签发, 工具系统只做绑定校验的不透明凭证 (ADR-0004 §6).

工具系统不解析信封的策略含义, 只回答四个机械问题: 有没有, 过没过期, 撤没撤销,
plan_hash 与执行画像对不对得上. 校验失败一律 fail closed.

信封是 ToolRuntime.execute 的必需参数, 不能为 None, 不能由工具伪造, 也不能因为工具是
pure, 只读或来自 trusted provider 而省略 —— 那正是旁路的来源.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.plan import TargetResolution, ToolPlan
from forgecli.shared.errors import ForgeError

__all__ = [
    "AuthorizationError",
    "AuthorizationErrorCode",
    "ExecutionAuthorization",
    "RevocationState",
    "validate_narrowing",
]


class AuthorizationErrorCode(Enum):
    """工具系统侧的授权失败码. 与安全裁决的结果空间 (allow/deny/ask) 分开."""

    AUTHORIZATION_MISSING = "authorization_missing"
    AUTHORIZATION_INVALID = "authorization_invalid"
    EXECUTION_ENVIRONMENT_CHANGED = "execution_environment_changed"
    SPEC_CAPABILITY_VIOLATION = "spec_capability_violation"


class RevocationState(Enum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    REVOKED = "revoked"


class AuthorizationError(ForgeError):
    """授权绑定校验失败. code 决定调用方是重新裁决还是直接回填 observation."""

    def __init__(self, code: AuthorizationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExecutionAuthorization:
    """一次调用的执行授权信封. 默认一次性: 重试是新请求, 产生新 plan_hash 和新授权."""

    authorization_id: str
    effective_plan: ToolPlan
    execution_profile_hash: str
    issued_at_epoch: float
    expires_at_epoch: float
    single_use: bool = True
    revocation_state: RevocationState = RevocationState.ACTIVE
    # ADR-0015 的恢复点标识 (需要真实工作区写入时才有).
    recovery_binding: str | None = None
    # ADR-0013 §14: 人类批准时看到的完整视图哈希 (走过 ASK 时才有).
    approval_view_hash: str | None = None
    # 派生字段: 与 effective_plan.plan_hash 恒等, 显式留字段是为了让审计与事件里
    # 有一个不必展开整个 plan 就能比对的锚点.
    plan_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.authorization_id.strip():
            raise ValueError("ExecutionAuthorization.authorization_id 不能为空")
        if self.expires_at_epoch <= self.issued_at_epoch:
            raise ValueError("ExecutionAuthorization 的有效期必须为正")
        object.__setattr__(self, "plan_hash", self.effective_plan.plan_hash)

    @property
    def envelope_hash(self) -> str:
        """信封本身的哈希, 进审计事件."""
        return digest(
            {
                "authorization_id": self.authorization_id,
                "plan_hash": self.plan_hash,
                "execution_profile_hash": self.execution_profile_hash,
                "recovery_binding": self.recovery_binding,
                "approval_view_hash": self.approval_view_hash,
                "expires_at_epoch": self.expires_at_epoch,
            }
        )

    def ensure_usable(self, *, now_epoch: float, execution_profile_hash: str) -> None:
        """执行前统一校验. 失败抛 AuthorizationError, 由 ToolRuntime 转成结构化结果."""
        if self.revocation_state is not RevocationState.ACTIVE:
            raise AuthorizationError(
                AuthorizationErrorCode.AUTHORIZATION_INVALID,
                f"授权已失效 (state={self.revocation_state.value})",
            )
        if now_epoch >= self.expires_at_epoch:
            raise AuthorizationError(
                AuthorizationErrorCode.AUTHORIZATION_INVALID, "授权已过期"
            )
        if execution_profile_hash != self.execution_profile_hash:
            raise AuthorizationError(
                AuthorizationErrorCode.EXECUTION_ENVIRONMENT_CHANGED,
                "执行环境画像与签发时不一致",
            )


def validate_narrowing(original: ToolPlan, effective: ToolPlan) -> None:
    """校验安全层的改写只做收缩 (ADR-0004 §6.1).

    允许: 能力集合取子集, 把 UNKNOWN / DYNAMIC 收紧为已封闭的目标集合.
    禁止: 换工具, 换 spec, 新增能力, 扩大目标集合, 把已封闭的目标重新打开.
    """
    if original.tool_name != effective.tool_name:
        raise AuthorizationError(
            AuthorizationErrorCode.AUTHORIZATION_INVALID, "改写不能更换工具"
        )
    if original.spec_hash != effective.spec_hash:
        raise AuthorizationError(
            AuthorizationErrorCode.AUTHORIZATION_INVALID, "改写不能更换 spec"
        )
    if not effective.capabilities <= original.capabilities:
        raise AuthorizationError(
            AuthorizationErrorCode.AUTHORIZATION_INVALID, "改写不能新增能力"
        )
    if not set(original.file_state_bindings) <= set(effective.file_state_bindings):
        raise AuthorizationError(
            AuthorizationErrorCode.AUTHORIZATION_INVALID,
            "改写不能移除或替换已有的文件状态绑定",
        )
    if original.target_resolution.closed:
        if not effective.target_resolution.closed:
            raise AuthorizationError(
                AuthorizationErrorCode.AUTHORIZATION_INVALID,
                "改写不能把已封闭的目标集合重新打开",
            )
        original_targets = set(original.effects.mutating_targets)
        if not set(effective.effects.mutating_targets) <= original_targets:
            raise AuthorizationError(
                AuthorizationErrorCode.AUTHORIZATION_INVALID, "改写不能扩大目标集合"
            )
    elif effective.target_resolution is TargetResolution.STATIC:
        # 原计划都没能证明目标集合, 改写却宣称目标"本来就是静态的"—— 这不是收缩,
        # 是把分析器的推导结果伪装成工具的原始声明. 只允许收到 FORGE_EXPANDED.
        raise AuthorizationError(
            AuthorizationErrorCode.AUTHORIZATION_INVALID,
            "未封闭的计划只能收紧为 forge_expanded",
        )
