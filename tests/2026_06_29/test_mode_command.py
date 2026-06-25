"""2026-06-29：模式切换通过普通斜杠命令 handler 更新 session。"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.session import (
    EventStore,
    EventType,
    SessionEvent,
    SessionService,
    SessionSnapshot,
    StateStore,
)
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.interfaces.cli.commands.mode_command import ModeCommand


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


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


def test_mode_command_records_mode_changed_without_generic_slash_event() -> None:
    events = _MemoryEventStore()
    session = SessionService(
        events,
        _MemoryStateStore(),
        workspace_root="/repo",
        clock=lambda: "2026-06-29T10:00:00+08:00",
        id_factory=lambda: "ses_test",
    )
    session.start()
    output = _RecordingOutput()

    changed = ModeCommand(SessionMode.PLAN, session, output).execute(
        SlashCommand(raw_text="/plan", command="plan")
    )

    assert changed is False
    assert [event.type for event in events.events] == [
        EventType.SESSION_CREATED,
        EventType.MODE_CHANGED,
    ]
    assert events.events[-1].payload == {"mode": "plan"}
    assert session.current().mode is SessionMode.PLAN
    assert output.lines == ["已切换到 [bold]plan[/] 模式。"]
