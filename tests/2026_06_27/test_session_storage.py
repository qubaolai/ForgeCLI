"""2026-06-27：会话事件日志与状态快照。"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.session import (
    EventStore,
    EventType,
    SessionEvent,
    SessionService,
    SessionSnapshot,
    StateStore,
)
from forgecli.domain.intents import SessionMode
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore


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
        for snapshot in reversed(self.snapshots):
            if snapshot.session_id == session_id:
                return snapshot
        return None


def _service() -> tuple[SessionService, _MemoryEventStore, _MemoryStateStore]:
    events = _MemoryEventStore()
    states = _MemoryStateStore()
    service = SessionService(
        events,
        states,
        workspace_root="/repo",
        clock=lambda: "2026-06-27T10:00:00+08:00",
        id_factory=lambda: "ses_test",
    )
    return service, events, states


def test_start_is_lazy_until_first_recordable_event() -> None:
    service, events, states = _service()

    snapshot = service.start()

    assert snapshot.session_id == "ses_test"
    assert snapshot.workspace_root == "/repo"
    assert snapshot.mode is SessionMode.CHAT
    assert events.events == []
    assert states.snapshots == []


def test_user_message_creates_session_event_then_updates_state() -> None:
    service, events, states = _service()
    service.start()

    event = service.record_user_message("hello", turn_id="turn_0001")

    assert event.event_id == "evt_0002"
    assert [stored.type for stored in events.events] == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
    ]
    assert events.events[0].payload == {"workspace_root": "/repo", "mode": "chat"}
    assert events.events[1].payload == {
        "turn_id": "turn_0001",
        "role": "user",
        "text": "hello",
    }
    assert states.snapshots[-1].last_event_id == "evt_0002"
    assert states.snapshots[-1].mode is SessionMode.CHAT


def test_mode_change_event_updates_snapshot_mode() -> None:
    service, events, states = _service()
    service.start()

    event = service.record_mode_change(SessionMode.PLAN)

    assert event.type is EventType.MODE_CHANGED
    assert event.payload == {"mode": "plan"}
    assert [stored.event_id for stored in events.events] == ["evt_0001", "evt_0002"]
    assert states.snapshots[-1].last_event_id == "evt_0002"
    assert states.snapshots[-1].mode is SessionMode.PLAN


def test_jsonl_event_store_and_state_store_round_trip(tmp_path: Path) -> None:
    events = JsonlEventStore(tmp_path / "sessions")
    states = JsonStateStore(tmp_path / "sessions")
    event = SessionEvent(
        event_id="evt_0001",
        session_id="ses_test",
        type=EventType.USER_MESSAGE,
        created_at="2026-06-27T10:00:00+08:00",
        payload={"text": "hello"},
    )
    snapshot = SessionSnapshot(
        session_id="ses_test",
        workspace_root="/repo",
        mode=SessionMode.CHAT,
        last_event_id="evt_0001",
        updated_at="2026-06-27T10:00:00+08:00",
    )

    events.append(event)
    states.write(snapshot)

    assert events.read("ses_test") == [event]
    assert states.read("ses_test") == snapshot
    assert (tmp_path / "sessions" / "ses_test" / "events.jsonl").exists()
    assert (tmp_path / "sessions" / "ses_test" / "state.json").exists()
    assert not (tmp_path / "sessions" / "ses_test" / "state.json.tmp").exists()
