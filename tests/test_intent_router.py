"""交互输入与斜杠命令路由的验收测试。

IntentRouter 只负责把输入解析成领域 intent；已注册的斜杠命令统一返回
SlashCommand，模式切换、配置和状态等业务语义都由 application handler 执行。
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from rich.console import Console

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.intent_router import IntentRouter
from forgecli.application.interaction_ports import DirectoryPicker
from forgecli.application.project import (
    ProjectConfig,
    ProjectContext,
    ProjectService,
)
from forgecli.application.session import (
    EventStore,
    SessionEvent,
    SessionService,
    SessionSnapshot,
    StateStore,
)
from forgecli.application.slash_commands import CommandRegistry
from forgecli.domain.intents import (
    IntentKind,
    SlashCommand,
    UnknownCommand,
    UserMessage,
)
from forgecli.infrastructure.project import (
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.wiring import build_registry


class _NullPicker(DirectoryPicker):
    def pick(self, list_subdirs: Callable[[str], Sequence[str]]) -> str | None:
        return None


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
        workspace_root="/work",
        clock=lambda: "2026-06-29T10:00:00+08:00",
        id_factory=lambda: "ses_test",
    )
    service.start()
    return service


def _context() -> ProjectContext:
    return ProjectContext(
        ProjectConfig(
            project_id="repo-test",
            trusted=True,
            primary_workspace_root="/work",
            workspace_roots=("/work",),
        )
    )


def _service() -> ProjectService:
    root = Path(tempfile.mkdtemp()) / "projects"
    return ProjectService(
        TomlProjectIndexStore(root / "index.toml"),
        TomlProjectConfigStore(root),
    )


def _registry() -> CommandRegistry:
    console = Console(record=True)
    output = RichOutput(console)
    session = _session()
    return build_registry(
        session,
        _context(),
        _service(),
        RichMenuPresenter(console),
        _NullPicker(),
        output,
        AgentTurnService(session),
    )


def _router() -> IntentRouter:
    return IntentRouter(_registry())


def test_registry_contains_current_roadmap_slash_commands() -> None:
    registered_names = {spec.name for spec in _registry().all_specs()}

    assert registered_names >= {
        "help",
        "chat",
        "plan",
        "act",
        "status",
        "config",
        "model",
        "add-dir",
        "resume",
    }
    assert "exit" not in registered_names
    assert "pause" not in registered_names
    assert all(spec.handler is not None for spec in _registry().all_specs())


@pytest.mark.parametrize(
    ("raw_text", "expected_text"),
    [
        ("请帮我检查这个仓库", "请帮我检查这个仓库"),
        ("  帮我解释 tests 目录  ", "  帮我解释 tests 目录  "),
        ("/help是做什么用的", "/help是做什么用的"),
        ("/usr/bin/env python", "/usr/bin/env python"),
    ],
)
def test_routes_natural_language_as_user_message(
    raw_text: str, expected_text: str
) -> None:
    intent = _router().route(raw_text)

    assert isinstance(intent, UserMessage)
    assert intent.kind is IntentKind.USER_MESSAGE
    assert intent.text == expected_text


@pytest.mark.parametrize(
    "command",
    ["help", "chat", "plan", "act", "status", "config", "model", "add-dir", "resume"],
)
def test_routes_registered_slash_commands_as_slash_command(command: str) -> None:
    intent = _router().route(f"/{command}")

    assert isinstance(intent, SlashCommand)
    assert intent.kind is IntentKind.SLASH_COMMAND
    assert intent.command == command
    assert intent.args == ()


def test_routes_bare_slash_as_help_command() -> None:
    intent = _router().route("/")

    assert isinstance(intent, SlashCommand)
    assert intent.command == "help"
    assert intent.args == ()


def test_routes_slash_command_arguments() -> None:
    intent = _router().route("/config set model.provider openai")

    assert isinstance(intent, SlashCommand)
    assert intent.command == "config"
    assert intent.args == ("set", "model.provider", "openai")


def test_normalizes_slash_command_name_to_lowercase() -> None:
    intent = _router().route("/HELP")

    assert isinstance(intent, SlashCommand)
    assert intent.command == "help"


@pytest.mark.parametrize("command", ["wat", "exit", "pause"])
def test_routes_unknown_or_removed_slash_command_with_help_hint(command: str) -> None:
    intent = _router().route(f"/{command}")

    assert isinstance(intent, UnknownCommand)
    assert intent.kind is IntentKind.UNKNOWN_COMMAND
    assert intent.command == command
    assert "/help" in intent.error_message


@pytest.mark.parametrize("raw_text", ["", "   ", "\n"])
def test_rejects_empty_input(raw_text: str) -> None:
    with pytest.raises(ValueError, match="raw_text 不能为空"):
        _router().route(raw_text)
