"""/status 展示 session 快照（session/mode/last_event）与工作区目录列表（cwd:）。"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.project import ProjectConfig, ProjectContext
from forgecli.application.session import SessionService
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore
from forgecli.interfaces.cli.commands.status_command import StatusCommand


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


def _context(*roots: str) -> ProjectContext:
    return ProjectContext(
        ProjectConfig(
            project_id="repo-deadbeef",
            trusted=True,
            primary_workspace_root=roots[0],
            workspace_roots=roots,
        )
    )


def _session(tmp_path: Path, root: str) -> SessionService:
    sessions = tmp_path / "sessions"
    service = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root=root,
    )
    service.start()
    return service


def test_status_shows_session_mode_and_workspace_list(tmp_path: Path) -> None:
    session = _session(tmp_path, "/work/primary")
    session.record_mode_change(SessionMode.PLAN)  # -> evt_0001 created, evt_0002 mode
    context = _context("/work/primary", "/work/extra")
    output = _RecordingOutput()

    StatusCommand(session, context, output).execute(
        SlashCommand(raw_text="/status", command="status")
    )

    text = output.lines[0]
    assert "session: " in text
    assert "mode: plan" in text
    assert "last_event: evt_0002" in text
    assert "cwd:" in text
    assert "- /work/primary" in text
    assert "- /work/extra" in text


def test_status_lists_at_least_primary_with_no_events(tmp_path: Path) -> None:
    session = _session(tmp_path, "/only/primary")
    output = _RecordingOutput()

    StatusCommand(session, _context("/only/primary"), output).execute(
        SlashCommand(raw_text="/status", command="status")
    )

    text = output.lines[0]
    assert "mode: chat" in text
    assert "last_event: -" in text  # 尚无事件
    assert "- /only/primary" in text
