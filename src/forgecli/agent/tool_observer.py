"""把执行链的运行观察翻译成 AgentRunEvent (ADR-0016 §4.3).

放在 agent 而不是 tools: 依赖方向是"观察者实现认识事件总线", 反过来会让执行链知道
终端时间线的存在.
"""

from __future__ import annotations

from forgecli.agent.run_bus import AgentRunEventBus
from forgecli.agent.run_events import (
    AgentRunEventKind,
    ApprovalRequestedPayload,
    ApprovalResolvedPayload,
    PolicyResolvedPayload,
    RunEventPayload,
    ToolCompletedPayload,
    ToolPreparedPayload,
    ToolStartedPayload,
)
from forgecli.agent.scrubbing import scrub_text
from forgecli.core.tool.observer import ToolRunObserver

__all__ = ["EventBusToolRunObserver"]


class EventBusToolRunObserver(ToolRunObserver):
    def __init__(self, bus: AgentRunEventBus) -> None:
        self._bus = bus
        self._turn_id = ""
        self._session_id = ""
        self._agent_id = ""

    def bind_turn(self, session_id: str, turn_id: str, agent_id: str = "") -> None:
        self._session_id = session_id
        self._turn_id = turn_id
        self._agent_id = agent_id

    def tool_validated(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        activity: str,
        arguments: tuple[tuple[str, str], ...],
        elapsed_ms: float,
    ) -> None:
        self._publish(
            AgentRunEventKind.TOOL_PREPARED,
            ToolPreparedPayload(
                tool_name=tool_name,
                activity=activity,
                arguments=tuple((name, scrub_text(value)) for name, value in arguments),
                elapsed_ms=elapsed_ms,
            ),
            invocation_id=tool_use_id,
        )

    def permission_decided(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        behavior: str,
        reason: str,
        message: str,
        elapsed_ms: float,
    ) -> None:
        self._publish(
            AgentRunEventKind.POLICY_RESOLVED,
            PolicyResolvedPayload(
                tool_name=tool_name,
                decision=behavior,
                reason=reason,
                detail=message,
                elapsed_ms=elapsed_ms,
            ),
            invocation_id=tool_use_id,
        )

    def permission_prompted(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        prompt_id: str,
        message: str,
        suggestions: tuple[str, ...],
    ) -> None:
        self._publish(
            AgentRunEventKind.APPROVAL_REQUESTED,
            ApprovalRequestedPayload(
                tool_name=tool_name,
                prompt_id=prompt_id,
                reason=message,
                suggestions=suggestions,
            ),
            invocation_id=tool_use_id,
        )

    def permission_answered(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        prompt_id: str,
        outcome: str,
        choice: str,
        note: str,
    ) -> None:
        self._publish(
            AgentRunEventKind.APPROVAL_RESOLVED,
            ApprovalResolvedPayload(
                tool_name=tool_name,
                outcome=outcome,
                prompt_id=prompt_id,
                choice=choice,
                note=note,
            ),
            invocation_id=tool_use_id,
        )

    def tool_started(self, tool_name: str, *, tool_use_id: str) -> None:
        self._publish(
            AgentRunEventKind.TOOL_STARTED,
            ToolStartedPayload(tool_name=tool_name),
            invocation_id=tool_use_id,
        )

    def tool_completed(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        status: str,
        elapsed_ms: float,
        result_summary: str,
        error_summary: str,
        bytes_out: int,
        persisted: bool,
        executed: bool,
    ) -> None:
        self._publish(
            AgentRunEventKind.TOOL_COMPLETED,
            ToolCompletedPayload(
                tool_name=tool_name,
                status=status,
                elapsed_ms=elapsed_ms,
                result_summary=scrub_text(result_summary),
                error_summary=scrub_text(error_summary),
                bytes_out=bytes_out,
                persisted=persisted,
                executed=executed,
            ),
            invocation_id=tool_use_id,
        )

    def tool_cancelled(
        self, tool_name: str, *, tool_use_id: str, elapsed_ms: float
    ) -> None:
        self._publish(
            AgentRunEventKind.TOOL_CANCELLED,
            ToolCompletedPayload(
                tool_name=tool_name,
                status="cancelled",
                elapsed_ms=elapsed_ms,
                # 不幂等的调用被打断时, "没成功"与"没发生"是两回事: 终端不能显示成
                # 普通失败 (ADR-0016 §10.1).
                side_effect_unknown=True,
            ),
            invocation_id=tool_use_id,
        )

    def _publish(
        self,
        kind: AgentRunEventKind,
        payload: RunEventPayload,
        *,
        invocation_id: str,
    ) -> None:
        if not self._turn_id or not self._session_id:
            # 没绑定轮次说明这次调用不在任何 turn 里: 与其发一条归属不明的事件,
            # 不如不发.
            return
        self._bus.publish(
            kind,
            session_id=self._session_id,
            turn_id=self._turn_id,
            agent_id=self._agent_id,
            payload=payload,
            invocation_id=invocation_id,
        )
