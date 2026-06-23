"""/status 展示当前模式与工作区目录列表（cwd: 列表）。"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.project import ProjectConfig, ProjectContext
from forgecli.application.session import SessionState
from forgecli.domain.intents import SessionMode, SlashCommand
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


def test_status_shows_mode_and_workspace_list() -> None:
    state = SessionState(mode=SessionMode.PLAN)
    context = _context("/work/primary", "/work/extra")
    output = _RecordingOutput()

    StatusCommand(state, context, output).execute(
        SlashCommand(raw_text="/status", command="status")
    )

    text = output.lines[0]
    assert "mode: plan" in text
    assert "cwd:" in text
    assert "- /work/primary" in text
    assert "- /work/extra" in text


def test_status_lists_at_least_primary() -> None:
    state = SessionState()
    output = _RecordingOutput()

    StatusCommand(state, _context("/only/primary"), output).execute(
        SlashCommand(raw_text="/status", command="status")
    )

    assert "- /only/primary" in output.lines[0]
