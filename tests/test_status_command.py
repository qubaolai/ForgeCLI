"""/status 展示当前模式与工作区目录列表（cwd: 列表）。"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.project import ProjectConfig, ProjectContext
from forgecli.application.session import (
    EventStore,
    SessionEvent,
    SessionService,
    SessionSnapshot,
    StateStore,
)
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.interfaces.cli.commands.status_command import StatusCommand


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


def _session() -> SessionService:
    service = SessionService(
        _MemoryEventStore(),
        _MemoryStateStore(),
        workspace_root="/work/primary",
        clock=lambda: "2026-06-27T10:00:00+08:00",
        id_factory=lambda: "ses_test",
    )
    service.start()
    return service


def _context(*roots: str) -> ProjectContext:
    return ProjectContext(
        ProjectConfig(
            project_id="repo-deadbeef",
            trusted=True,
            primary_workspace_root=roots[0],
            workspace_roots=roots,
        )
    )


def test_status_shows_mode_and_workspace_list() -> None:
    session = _session()
    session.record_mode_change(SessionMode.PLAN)
    context = _context("/work/primary", "/work/extra")
    output = _RecordingOutput()

    StatusCommand(session, context, output).execute(
        SlashCommand(raw_text="/status", command="status")
    )

    text = output.lines[0]
    assert "session: ses_test" in text
    assert "mode: plan" in text
    assert "last_event: evt_0002" in text
    assert "cwd:" in text
    assert "- /work/primary" in text
    assert "- /work/extra" in text


def test_status_lists_at_least_primary() -> None:
    session = _session()
    output = _RecordingOutput()

    StatusCommand(session, _context("/only/primary"), output).execute(
        SlashCommand(raw_text="/status", command="status")
    )

    assert "- /only/primary" in output.lines[0]
