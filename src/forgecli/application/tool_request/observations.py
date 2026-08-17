"""回填模型的结构化 observation (ADR-0004 §8, ADR-0013 §2).

**策略结果不是 ToolResult.** policy_denied, approval_required 和
execution_environment_changed 都由协调器产出为 observation, 与工具执行结果分属不同的
类型空间 —— 混在一起, 模型就会把"被安全策略拒绝"当成"工具坏了"然后反复重试.

can_retry 是给模型的明确信号: Hard Deny 是 false (改写命令重试属于绕过尝试), 环境变化
是 true (重新裁决即可).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.agent.actions import (
    LoopObservation,
    ObservationDisposition,
    ObservationSource,
)
from forgecli.domain.tool.result import ToolResult

__all__ = ["ObservationKind", "ToolObservation"]


class ObservationKind(Enum):
    TOOL_RESULT = "tool_result"
    POLICY_DENIED = "policy_denied"
    # 需要重新审批 (批准后事实变化). 重试有意义.
    APPROVAL_REQUIRED = "approval_required"
    # 人类明确拒绝. 终止性的.
    APPROVAL_DENIED = "approval_denied"
    # 无人可裁决 (非交互环境 / EOF). 不是拒绝, 是没人回答 —— 重试永远还是这个结果.
    APPROVAL_UNAVAILABLE = "approval_unavailable"
    TOOL_UNAVAILABLE_IN_MODE = "tool_unavailable_in_mode"
    TOOL_UNAVAILABLE = "tool_unavailable"
    PREPARATION_FAILED = "preparation_failed"
    AUTHORIZATION_MISSING = "authorization_missing"
    EXECUTION_ENVIRONMENT_CHANGED = "execution_environment_changed"
    RECOVERY_UNAVAILABLE = "recovery_unavailable"
    OUTCOME_UNKNOWN = "outcome_unknown"

    @property
    def disposition(self) -> ObservationDisposition:
        """这条结论对后续工具调用意味着什么 (ADR-0013 §4 的人机边界).

        人类拒绝的是**意图**而不是那一条命令: 允许模型换个说法重试, 等于让它绕过人刚
        做的决定, 审批提示就成了摆设. 而策略拒绝说的是"这条路不通", 换条合法的路正是
        我们希望它做的 —— 所以两者分流, 判据是**谁说的不行**, 不是 mode.
        """
        if self in _HALTING_KINDS:
            return ObservationDisposition.HALT
        if self in _BLOCKING_KINDS:
            return ObservationDisposition.BLOCKED
        return ObservationDisposition.CONTINUE

    @property
    def source(self) -> ObservationSource:
        if self is ObservationKind.TOOL_RESULT:
            return ObservationSource.TOOL
        if self in _SECURITY_KINDS:
            return ObservationSource.SECURITY
        return ObservationSource.ERROR


_SECURITY_KINDS = frozenset(
    {
        ObservationKind.POLICY_DENIED,
        ObservationKind.APPROVAL_REQUIRED,
        ObservationKind.APPROVAL_DENIED,
        ObservationKind.APPROVAL_UNAVAILABLE,
        ObservationKind.TOOL_UNAVAILABLE_IN_MODE,
        ObservationKind.RECOVERY_UNAVAILABLE,
    }
)

# 人做了决定, 或者根本没有人 —— 两种情况下继续派工具都没有意义.
_HALTING_KINDS = frozenset(
    {
        ObservationKind.APPROVAL_DENIED,
        ObservationKind.APPROVAL_UNAVAILABLE,
    }
)

# 机器说这条路不通. 换条路是合理的, 但要计数 —— 否则模型会一直换着花样撞墙.
_BLOCKING_KINDS = frozenset(
    {
        ObservationKind.POLICY_DENIED,
        ObservationKind.APPROVAL_REQUIRED,
        ObservationKind.RECOVERY_UNAVAILABLE,
        ObservationKind.AUTHORIZATION_MISSING,
    }
)


@dataclass(frozen=True)
class ToolObservation:
    """一次工具请求的最终结论. 无论成功, 被拒还是待审批, 都经这一个类型回填."""

    kind: ObservationKind
    message: str
    invocation_id: str
    tool_name: str
    reason_code: str = ""
    can_retry: bool = False
    plan_hash: str | None = None
    authorization_id: str | None = None
    approval_id: str | None = None
    checkpoint_id: str | None = None
    result: ToolResult | None = None
    risk_summary: tuple[str, ...] = field(default=())

    @property
    def is_error(self) -> bool:
        if self.kind is ObservationKind.TOOL_RESULT:
            return self.result is not None and self.result.error is not None
        return True

    def to_loop_observation(self) -> LoopObservation:
        return LoopObservation(
            content=self.render(),
            source=self.kind.source,
            is_error=self.is_error,
            disposition=self.kind.disposition,
        )

    def render(self) -> str:
        """给模型看的文本. 工具结果直接给内容, 其余给结构化拒绝说明."""
        if self.kind is ObservationKind.TOOL_RESULT and self.result is not None:
            return self.result.text
        lines = [f"[{self.kind.value}] {self.message}"]
        if self.reason_code:
            lines.append(f"reason: {self.reason_code}")
        lines.extend(f"risk: {item}" for item in self.risk_summary)
        lines.append(f"can_retry: {str(self.can_retry).lower()}")
        return "\n".join(lines)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self.kind.value,
            "invocation_id": self.invocation_id,
            "tool_name": self.tool_name,
            "message": self.message,
            "can_retry": self.can_retry,
        }
        if self.reason_code:
            payload["reason_code"] = self.reason_code
        if self.plan_hash:
            payload["plan_hash"] = self.plan_hash
        if self.authorization_id:
            payload["authorization_id"] = self.authorization_id
        if self.approval_id:
            payload["approval_id"] = self.approval_id
        if self.checkpoint_id:
            payload["checkpoint_id"] = self.checkpoint_id
        if self.result is not None:
            payload["result"] = self.result.to_audit_payload()
        return payload
