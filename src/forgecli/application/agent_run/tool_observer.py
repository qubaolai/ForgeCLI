"""把工具链路的运行观察翻译成 AgentRunEvent (ADR-0016 §4.3).

放在 agent_run 而不是 tool_request: 依赖方向是"观察者实现认识事件总线", 反过来会让
工具链路知道终端时间线的存在.
"""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.tool_request.run_observer import ToolRunObserver
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    ApprovalRequestedPayload,
    ApprovalResolvedPayload,
    PolicyResolvedPayload,
    RunEventPayload,
    ToolCompletedPayload,
    ToolPreparedPayload,
    ToolStartedPayload,
)
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult

__all__ = ["EventBusToolRunObserver"]


def _arguments_of(arguments: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    """入参逐字进事件, 只做控制字符清理.

    不做脱敏, 也不留 allowlist 之类的开关: 工具入参是用户判断"要不要让这次调用发生"的
    依据, 挡掉一部分只会让他在看不全的信息上做决定. 控制字符仍然要剥 —— 那不是隐藏
    内容, 是防止参数里的 ANSI 序列重画终端.
    """
    return tuple(
        (name, _scrub(str(value))) for name, value in sorted(arguments.items())
    )


def _scrub(text: str) -> str:
    return "".join(
        char for char in text if char == "\t" or (char >= " " and char != "\x7f")
    )


def _summary_of(result: ToolResult) -> str:
    """机制摘要, 不含工具输出本身."""
    parts = [f"{result.metrics.bytes_out} 字节"]
    if result.metrics.exit_code is not None:
        parts.append(f"退出码 {result.metrics.exit_code}")
    if result.artifacts:
        parts.append(f"{len(result.artifacts)} 个产物")
    if any(part.truncated for part in result.content_parts):
        parts.append("输出已截断")
    return " · ".join(parts)


class EventBusToolRunObserver(ToolRunObserver):
    def __init__(self, bus: AgentRunEventBus) -> None:
        self._bus = bus
        self._turn_id = ""

    def bind_turn(self, turn_id: str) -> None:
        self._turn_id = turn_id

    def tool_prepared(
        self, plan: ToolPlan, *, invocation_id: str, arguments: Mapping[str, object]
    ) -> None:
        self._publish(
            AgentRunEventKind.TOOL_PREPARED,
            ToolPreparedPayload(
                tool_name=plan.tool_name,
                capabilities=tuple(sorted(item.value for item in plan.capabilities)),
                target_count=len(plan.effects.mutating_targets)
                + len(plan.effects.read_paths),
                target_resolution=plan.target_resolution.value,
                arguments=_arguments_of(arguments),
            ),
            invocation_id=invocation_id,
        )

    def policy_resolved(
        self, decision: AuthorizationDecision, *, invocation_id: str
    ) -> None:
        self._publish(
            AgentRunEventKind.POLICY_RESOLVED,
            PolicyResolvedPayload(
                tool_name=decision.effective_plan.tool_name,
                decision=decision.decision.value,
                reason=decision.reason.value,
                mandatory=decision.mandatory,
            ),
            invocation_id=invocation_id,
        )

    def approval_requested(
        self, tool_name: str, *, invocation_id: str, mandatory: bool, target_count: int
    ) -> None:
        self._publish(
            AgentRunEventKind.APPROVAL_REQUESTED,
            ApprovalRequestedPayload(
                tool_name=tool_name, mandatory=mandatory, target_count=target_count
            ),
            invocation_id=invocation_id,
        )

    def approval_resolved(
        self,
        tool_name: str,
        *,
        invocation_id: str,
        outcome: str,
        scope: ApprovalScope | None = None,
    ) -> None:
        self._publish(
            AgentRunEventKind.APPROVAL_RESOLVED,
            ApprovalResolvedPayload(
                tool_name=tool_name,
                outcome=outcome,
                scope="" if scope is None else scope.value,
            ),
            invocation_id=invocation_id,
        )

    def tool_started(self, tool_name: str, *, invocation_id: str) -> None:
        self._publish(
            AgentRunEventKind.TOOL_STARTED,
            ToolStartedPayload(tool_name=tool_name),
            invocation_id=invocation_id,
        )

    def tool_completed(
        self, result: ToolResult, *, invocation_id: str, elapsed_ms: float
    ) -> None:
        self._publish(
            AgentRunEventKind.TOOL_COMPLETED,
            ToolCompletedPayload(
                tool_name=result.tool_name,
                status=result.status.value,
                elapsed_ms=elapsed_ms,
                # 只报机制摘要: 字节数, 退出码, 产物数. 完整 stdout / 文件内容默认
                # 不回显, 由 artifact 承载 (ADR-0016 §7.2).
                result_summary=_summary_of(result),
                error_summary="" if result.error is None else result.error.message,
            ),
            invocation_id=invocation_id,
        )

    def tool_cancelled(
        self, tool_name: str, *, invocation_id: str, side_effect_unknown: bool
    ) -> None:
        self._publish(
            AgentRunEventKind.TOOL_CANCELLED,
            ToolCompletedPayload(
                tool_name=tool_name,
                status="cancelled",
                # 不幂等的调用被打断时, "没成功"与"没发生"是两回事: 终端不能显示成
                # 普通失败 (ADR-0016 §10.1).
                side_effect_unknown=side_effect_unknown,
            ),
            invocation_id=invocation_id,
        )

    def _publish(
        self,
        kind: AgentRunEventKind,
        payload: RunEventPayload,
        *,
        invocation_id: str,
    ) -> None:
        if not self._turn_id:
            # 没绑定轮次说明这次调用不在任何 turn 里 (slash command 直接调用之类):
            # 与其发一条归属不明的事件, 不如不发.
            return
        self._bus.publish(
            kind,
            turn_id=self._turn_id,
            payload=payload,
            invocation_id=invocation_id,
        )
