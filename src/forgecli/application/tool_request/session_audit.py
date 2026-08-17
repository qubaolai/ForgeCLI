"""把工具与安全审计写进会话事件日志.

事件全部经 SessionService 这一个门面落盘 (与 ADR-0003 的约定一致), 因此工具执行的审计
与对话事件天然在同一条时间线上, resume 时能一起重放.

payload 里只放安全摘要: 不写命令原文以外的敏感内容, 不写凭证, 不写恢复 blob.
"""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.application.session import SessionService
from forgecli.application.tool_request.audit import ToolAuditSink
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.session.events import EventType
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult

__all__ = ["SessionToolAudit"]

_EVENT_NAMES: dict[str, EventType] = {
    "approval_requested": EventType.APPROVAL_REQUESTED,
    "approval_resolved": EventType.APPROVAL_RESOLVED,
    "classifier_invoked": EventType.CLASSIFIER_INVOKED,
    "checkpoint_created": EventType.CHECKPOINT_CREATED,
    "mutation_recorded": EventType.MUTATION_RECORDED,
    "recovery_performed": EventType.RECOVERY_PERFORMED,
    "dir_grant_changed": EventType.DIR_GRANT_CHANGED,
}


class SessionToolAudit(ToolAuditSink):
    def __init__(self, session: SessionService, *, turn_id: str = "") -> None:
        self._session = session
        self._turn_id = turn_id

    def bind_turn(self, turn_id: str) -> None:
        # 由 ToolRequestCoordinator.handle 在每次调用入口调用 (ADR-0016 §9).
        self._turn_id = turn_id

    def tool_requested(
        self, plan: ToolPlan, *, invocation_id: str, authorization_id: str
    ) -> None:
        self._session.record_tool_event(
            EventType.TOOL_REQUESTED,
            {
                "invocation_id": invocation_id,
                "tool_name": plan.tool_name,
                "spec_hash": plan.spec_hash,
                "plan_hash": plan.plan_hash,
                "target_set_hash": plan.target_set_hash,
                "authorization_id": authorization_id,
                "capabilities": sorted(item.value for item in plan.capabilities),
            },
            turn_id=self._turn_id,
        )

    def tool_completed(self, result: ToolResult, *, plan_hash: str) -> None:
        self._session.record_tool_event(
            EventType.TOOL_COMPLETED,
            {**result.to_audit_payload(), "plan_hash": plan_hash},
            turn_id=self._turn_id,
        )

    def policy_decision(
        self, decision: AuthorizationDecision, *, invocation_id: str
    ) -> None:
        self._session.record_tool_event(
            EventType.POLICY_DECISION,
            {**decision.to_audit_payload(), "invocation_id": invocation_id},
            turn_id=self._turn_id,
        )

    def approval_event(self, name: str, payload: Mapping[str, object]) -> None:
        self._emit(name, payload)

    def recovery_event(self, name: str, payload: Mapping[str, object]) -> None:
        self._emit(name, payload)

    def _emit(self, name: str, payload: Mapping[str, object]) -> None:
        event_type = _EVENT_NAMES.get(name)
        if event_type is None:
            return
        self._session.record_tool_event(event_type, payload, turn_id=self._turn_id)
