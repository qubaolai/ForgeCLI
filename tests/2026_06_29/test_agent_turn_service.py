"""2026-06-29：AgentTurnService stub 与 session mode 解耦验证。"""

from __future__ import annotations

import pytest

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.session import (
    EventStore,
    EventType,
    SessionEvent,
    SessionService,
    SessionSnapshot,
    StateStore,
)
from forgecli.domain.conversation import TurnStatus
from forgecli.domain.intents import SessionMode


class _MemoryEventStore(EventStore):
    def __init__(self) -> None:
        self.events: list[SessionEvent] = []

    def append(self, event: SessionEvent) -> None:
        self.events.append(event)

    def read(self, session_id: str) -> list[SessionEvent]:
        return [event for event in self.events if event.session_id == session_id]


class _MemoryStateStore(StateStore):
    def __init__(self) -> None:
        self.snapshots: list[SessionSnapshot] = []

    def write(self, snapshot: SessionSnapshot) -> None:
        self.snapshots.append(snapshot)

    def read(self, session_id: str) -> SessionSnapshot | None:
        return self.snapshots[-1] if self.snapshots else None


def _service() -> tuple[SessionService, _MemoryEventStore]:
    events = _MemoryEventStore()
    states = _MemoryStateStore()
    service = SessionService(
        events,
        states,
        workspace_root="/repo",
        clock=lambda: "2026-06-29T10:00:00+08:00",
        id_factory=lambda: "ses_test",
    )
    service.start()
    return service, events


@pytest.mark.parametrize("mode", [SessionMode.CHAT, SessionMode.PLAN, SessionMode.ACT])
def test_handle_user_message_records_pair_using_current_mode(mode: SessionMode) -> None:
    service, events = _service()
    if mode is not SessionMode.CHAT:
        service.record_mode_change(mode)

    agent = AgentTurnService(
        service,
        reply=lambda text, current_mode: f"{current_mode.value}:{text}",
    )

    response = agent.handle_user_message("检查状态")

    assert response.text == f"{mode.value}:检查状态"
    assert response.status is TurnStatus.COMPLETED
    assert [event.type for event in events.events[-2:]] == [
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
    ]
    user_event, assistant_event = events.events[-2:]
    assert user_event.payload == {
        "turn_id": "turn_0001",
        "role": "user",
        "text": "检查状态",
    }
    assert assistant_event.payload == {
        "turn_id": "turn_0001",
        "role": "assistant",
        "status": "completed",
        "text": f"{mode.value}:检查状态",
    }
    assert service.current().mode is mode


def test_handle_user_message_records_failed_assistant_when_reply_raises() -> None:
    service, events = _service()

    def _raise(_text: str, _mode: SessionMode) -> str:
        raise RuntimeError("provider down")

    response = AgentTurnService(service, reply=_raise).handle_user_message("hello")

    assert response.status is TurnStatus.FAILED
    assert response.text == "助手处理出错(stub)"
    assert [event.type for event in events.events] == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
    ]
    assert events.events[-1].payload["status"] == "failed"
    assert events.events[-1].payload["turn_id"] == "turn_0001"
