"""2026-07-01：/resume slash command stub。"""

from __future__ import annotations

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.menu import Menu
from forgecli.application.project import ProjectConfig, ProjectContext
from forgecli.application.session.event_store import EventStore
from forgecli.application.session.events import EventType, SessionEvent
from forgecli.application.session.resume_service import ResumeService
from forgecli.application.session.session_catalog import SessionCatalog
from forgecli.application.session.session_service import SessionService
from forgecli.application.session.snapshot import SessionSnapshot
from forgecli.application.session.state_store import StateStore
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.interfaces.cli.commands.resume_command import ResumeCommand


class _MemoryCatalog(SessionCatalog):
    def __init__(self, session_ids: list[str]) -> None:
        self._session_ids = session_ids

    def list_session_ids(self) -> list[str]:
        return list(self._session_ids)


class _MemoryEventStore(EventStore):
    def __init__(self) -> None:
        self.events: list[SessionEvent] = []

    def append(self, event: SessionEvent) -> None:
        self.events.append(event)

    def read(self, session_id: str) -> list[SessionEvent]:
        return [event for event in self.events if event.session_id == session_id]


class _MemoryStateStore(StateStore):
    def __init__(self) -> None:
        self.snapshots: dict[str, SessionSnapshot] = {}

    def write(self, snapshot: SessionSnapshot) -> None:
        self.snapshots[snapshot.session_id] = snapshot

    def read(self, session_id: str) -> SessionSnapshot | None:
        return self.snapshots.get(session_id)


class _CapturingPresenter(MenuPresenter):
    def __init__(self, *, select_first: bool = False) -> None:
        self.presented: Menu | None = None
        self._select_first = select_first

    def present(self, menu: Menu) -> None:
        self.presented = menu
        if self._select_first and menu.choices:
            on_select = menu.choices[0].on_select
            if on_select is not None:
                on_select()


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


def _snapshot(
    session_id: str,
    *,
    workspace_root: str = "/repo",
    title: str = "Resume target",
    last_event_id: str = "evt_0003",
) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        workspace_root=workspace_root,
        mode=SessionMode.CHAT,
        last_event_id=last_event_id,
        updated_at="2026-07-01T10:00:00+08:00",
        title=title,
    )


def _event(
    session_id: str,
    event_id: str,
    event_type: EventType,
    payload: dict[str, object],
) -> SessionEvent:
    return SessionEvent(
        event_id=event_id,
        session_id=session_id,
        type=event_type,
        created_at="2026-07-01T10:00:00+08:00",
        payload=payload,
    )


def _history(session_id: str) -> list[SessionEvent]:
    return [
        _event(
            session_id,
            "evt_0001",
            EventType.SESSION_CREATED,
            {"workspace_root": "/repo", "mode": "chat"},
        ),
        _event(
            session_id,
            "evt_0002",
            EventType.USER_MESSAGE,
            {"turn_id": "turn_0001", "role": "user", "text": "hello"},
        ),
        _event(
            session_id,
            "evt_0003",
            EventType.ASSISTANT_MESSAGE,
            {
                "turn_id": "turn_0001",
                "role": "assistant",
                "status": "completed",
                "text": "stub",
            },
        ),
    ]


def _context(workspace_root: str = "/repo") -> ProjectContext:
    return ProjectContext(
        ProjectConfig(
            project_id="repo-deadbeef",
            trusted=True,
            primary_workspace_root=workspace_root,
            workspace_roots=(workspace_root,),
        )
    )


def _runtime(
    *,
    presenter: _CapturingPresenter | None = None,
    workspace_root: str = "/repo",
) -> tuple[
    ResumeCommand,
    _RecordingOutput,
    _CapturingPresenter,
    SessionService,
    AgentTurnService,
    _MemoryEventStore,
    _MemoryStateStore,
]:
    session_id = "ses_target"
    states = _MemoryStateStore()
    states.write(_snapshot(session_id, workspace_root=workspace_root))
    events = _MemoryEventStore()
    for event in _history(session_id):
        events.append(event)
    service = ResumeService(_MemoryCatalog([session_id]), states, events)
    session = SessionService(
        events,
        states,
        workspace_root="/repo",
        clock=lambda: "2026-07-01T10:01:00+08:00",
        id_factory=lambda: "ses_new",
    )
    session.start()
    agent = AgentTurnService(session, reply=lambda text, _mode: f"ok:{text}")
    actual_presenter = presenter or _CapturingPresenter()
    output = _RecordingOutput()
    command = ResumeCommand(
        service,
        session,
        agent,
        _context(),
        actual_presenter,
        output,
    )
    return command, output, actual_presenter, session, agent, events, states


def test_resume_without_args_presents_history_choices() -> None:
    command, output, presenter, *_unused = _runtime()

    command.execute(SlashCommand(raw_text="/resume", command="resume"))

    assert output.lines == []
    assert presenter.presented is not None
    assert presenter.presented.title == "恢复历史会话"
    choice = presenter.presented.choices[0]
    assert choice.label == "Resume target"
    assert choice.preview is not None
    assert choice.preview() == "chat · 2026-07-01T10:00:00+08:00"
    assert choice.payload is not None
    assert "session: ses_target" in choice.payload()
    assert choice.close_on_select is True


def test_resume_by_session_id_hydrates_session_and_agent_turn_counter() -> None:
    command, output, _presenter, session, agent, events, states = _runtime()

    command.execute(
        SlashCommand(
            raw_text="/resume ses_target",
            command="resume",
            args=("ses_target",),
        )
    )
    response = agent.handle_user_message("next")

    assert "已恢复会话：" in output.lines[0]
    assert response.text == "ok:next"
    assert session.current().session_id == "ses_target"
    assert [event.event_id for event in events.events[-2:]] == [
        "evt_0004",
        "evt_0005",
    ]
    assert events.events[-2].payload["turn_id"] == "turn_0002"
    assert events.events[-1].payload["turn_id"] == "turn_0002"
    assert states.read("ses_target").last_event_id == "evt_0005"


def test_resume_menu_selection_triggers_restore() -> None:
    presenter = _CapturingPresenter(select_first=True)
    command, output, _presenter, session, *_unused = _runtime(presenter=presenter)

    command.execute(SlashCommand(raw_text="/resume", command="resume"))

    assert presenter.presented is not None
    assert "已恢复会话：" in output.lines[0]
    assert session.current().session_id == "ses_target"


def test_resume_rejects_cross_workspace_history() -> None:
    command, output, _presenter, session, *_unused = _runtime(workspace_root="/other")

    command.execute(
        SlashCommand(
            raw_text="/resume ses_target",
            command="resume",
            args=("ses_target",),
        )
    )

    assert "跨目录(项目)恢复暂未实现" in output.lines[0]
    assert session.current().session_id == "ses_new"
