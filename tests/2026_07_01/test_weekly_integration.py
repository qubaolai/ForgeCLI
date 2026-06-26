"""2026-07-01：本周 CLI / 配置 / 模型 / session / resume 集成路径。"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.config.config_service import ConfigService
from forgecli.application.intent_router import IntentRouter
from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.menu import Choice, Menu
from forgecli.application.project import ProjectConfig, ProjectContext
from forgecli.application.session.resume_service import ResumeService
from forgecli.application.session.session_service import SessionService
from forgecli.application.slash_commands.registry import CommandRegistry, CommandSpec
from forgecli.domain.intents import SlashCommand
from forgecli.infrastructure.config import TomlConfigStore
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.infrastructure.session.fs_session_catalog import FsSessionCatalog
from forgecli.infrastructure.session.json_state_store import JsonStateStore
from forgecli.infrastructure.session.jsonl_event_store import JsonlEventStore
from forgecli.interfaces.cli.commands.config_command import ConfigCommand
from forgecli.interfaces.cli.commands.model_command import ModelsCommand
from forgecli.interfaces.cli.commands.resume_command import ResumeCommand
from forgecli.interfaces.cli.commands.status_command import StatusCommand
from forgecli.interfaces.cli.repl import Repl


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


def _find(menu: Menu, label: str) -> Choice:
    return next(choice for choice in menu.choices if choice.label == label)


def _context(workspace_root: str) -> ProjectContext:
    return ProjectContext(
        ProjectConfig(
            project_id="repo-deadbeef",
            trusted=True,
            primary_workspace_root=workspace_root,
            workspace_roots=(workspace_root,),
        )
    )


def _registry(
    *,
    session: SessionService,
    agent: AgentTurnService,
    context: ProjectContext,
    resume: ResumeService,
    presenter: MenuPresenter,
    output: UserOutput,
) -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            "status",
            "查看状态",
            handler=StatusCommand(session, context, output),
        )
    )
    registry.register(
        CommandSpec(
            "resume",
            "恢复历史会话",
            handler=ResumeCommand(resume, session, agent, context, presenter, output),
        )
    )
    return registry


def _repl(
    *,
    session: SessionService,
    agent: AgentTurnService,
    context: ProjectContext,
    resume: ResumeService,
    presenter: MenuPresenter,
    output: UserOutput,
) -> Repl:
    registry = _registry(
        session=session,
        agent=agent,
        context=context,
        resume=resume,
        presenter=presenter,
        output=output,
    )
    return Repl(
        console=Console(record=True),
        router=IntentRouter(registry),
        registry=registry,
        output=output,
        session=session,
        agent_turn=agent,
    )


def test_weekly_flow_config_model_session_status_and_resume(
    tmp_path: Path, monkeypatch
) -> None:
    forge_home = tmp_path / ".forge"
    workspace_root = str(tmp_path / "repo")
    config = ConfigService(
        TomlConfigStore(forge_home / "config.toml"),
        TomlConfigStore(forge_home / "projects" / "repo-deadbeef" / "forge.toml"),
    )
    llm = LlmConfigService(TomlLlmConfigStore(forge_home / "llm.toml"))
    llm.add_model("deepseek", "deepseek-chat", {"context_window": 65536})

    config_presenter = _CapturingPresenter()
    config_output = _RecordingOutput()
    ConfigCommand(config, llm, config_presenter, config_output).execute(
        SlashCommand(raw_text="/config", command="config")
    )
    assert config_presenter.presented is not None
    theme = _find(config_presenter.presented, "输出主题")
    assert theme.on_cycle is not None
    theme.on_cycle(1)
    assert config.effective().output_theme == "light"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    model_presenter = _CapturingPresenter()
    model_output = _RecordingOutput()
    ModelsCommand(config, llm, model_presenter, model_output).execute(
        SlashCommand(raw_text="/model", command="model")
    )
    assert model_presenter.presented is not None
    provider = _find(model_presenter.presented, "DeepSeek")
    assert provider.submenu is not None
    model = _find(provider.submenu(), "deepseek-chat")
    assert model.on_select is not None
    model.on_select()
    assert str(config.effective().default_model) == "deepseek:deepseek-chat"

    sessions_dir = forge_home / "projects" / "repo-deadbeef" / "sessions"
    states = JsonStateStore(sessions_dir)
    events = JsonlEventStore(sessions_dir)
    resume = ResumeService(FsSessionCatalog(sessions_dir), states, events)
    context = _context(workspace_root)
    session = SessionService(
        events,
        states,
        workspace_root=workspace_root,
        clock=lambda: "2026-07-01T10:00:00+08:00",
        id_factory=lambda: "ses_week",
    )
    session.start()
    output = _RecordingOutput()
    agent = AgentTurnService(session, reply=lambda text, _mode: f"stub:{text}")
    repl = _repl(
        session=session,
        agent=agent,
        context=context,
        resume=resume,
        presenter=_CapturingPresenter(),
        output=output,
    )

    repl._process_line("hello forge")
    repl._process_line("/status")

    first_history = events.read("ses_week")
    assert [event.type.value for event in first_history] == [
        "session_created",
        "user_message",
        "assistant_message",
    ]
    assert first_history[1].payload["text"] == "hello forge"
    assert states.read("ses_week").title.startswith("hello")
    assert "session: ses_week" in output.lines[-1]
    assert "last_event: evt_0003" in output.lines[-1]

    resumed_session = SessionService(
        events,
        states,
        workspace_root=workspace_root,
        clock=lambda: "2026-07-01T10:05:00+08:00",
        id_factory=lambda: "ses_new",
    )
    resumed_session.start()
    resumed_agent = AgentTurnService(
        resumed_session, reply=lambda text, _mode: f"resumed:{text}"
    )
    resumed_output = _RecordingOutput()
    resumed_repl = _repl(
        session=resumed_session,
        agent=resumed_agent,
        context=context,
        resume=resume,
        presenter=_CapturingPresenter(select_first=True),
        output=resumed_output,
    )

    resumed_repl._process_line("/resume")
    resumed_repl._process_line("continue")

    resumed_history = events.read("ses_week")
    assert "已恢复会话：" in resumed_output.lines[0]
    assert [event.event_id for event in resumed_history[-2:]] == [
        "evt_0004",
        "evt_0005",
    ]
    assert resumed_history[-2].payload["turn_id"] == "turn_0002"
    assert states.read("ses_week").last_event_id == "evt_0005"
