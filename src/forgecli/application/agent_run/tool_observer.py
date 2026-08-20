"""把工具链路的运行观察翻译成 AgentRunEvent (ADR-0016 §4.3).

放在 agent_run 而不是 tool_request: 依赖方向是"观察者实现认识事件总线", 反过来会让
工具链路知道终端时间线的存在.
"""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_run.scrubbing import scrub_arguments, scrub_text
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


# 一次调用最多逐条列出多少个目标. 超了只列前若干个并让 target_count 说出完整数量 ——
# 一次递归删除可能有几千个路径, 全塞进事件既没人看得完, 也会把 SSE 帧撑大.
_MAX_LISTED_TARGETS = 40


def _targets_of(plan: ToolPlan) -> tuple[str, ...]:
    paths = (*plan.effects.mutating_targets, *plan.effects.read_paths)
    return tuple(paths[:_MAX_LISTED_TARGETS])


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
                arguments=scrub_arguments(arguments),
                targets=_targets_of(plan),
                workspace_scope=plan.workspace_scope.value,
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
                matched_rule_id=decision.matched_rule_id or "",
                risk_facts=tuple(fact.code for fact in decision.risk_facts),
                detail=scrub_text(decision.message),
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

    def tool_rejected(
        self, tool_name: str, *, invocation_id: str, reason_code: str, message: str
    ) -> None:
        # 复用 TOOL_COMPLETED: 对展示层来说这就是这次调用的终态, 只是没有执行过。
        self._publish(
            AgentRunEventKind.TOOL_COMPLETED,
            ToolCompletedPayload(
                tool_name=tool_name,
                status=reason_code,
                elapsed_ms=0.0,
                error_summary=scrub_text(message),
                error_code=reason_code,
                executed=False,
            ),
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
                error_code="" if result.error is None else result.error.code,
                exit_code=result.metrics.exit_code,
                bytes_out=result.metrics.bytes_out,
                artifact_count=len(result.artifacts),
                truncated=any(part.truncated for part in result.content_parts),
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
                error_code="cancelled",
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
