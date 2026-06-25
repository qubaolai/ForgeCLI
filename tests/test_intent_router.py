"""交互输入与斜杠命令路由的验收测试。

测试分三层：
    1. registry 是否注册了 roadmap 要求的命令面。
    2. IntentRouter 是否把单行输入解析为稳定的领域 intent。
    3. REPL stub 是否复用 IntentRouter，并只做内存态更新与安全退出。
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from rich.console import Console

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
    SessionState,
    StateStore,
)
from forgecli.application.slash_commands import CommandRegistry
from forgecli.domain.intents import (
    ControlAction,
    ControlSignal,
    IntentKind,
    ModeChange,
    SessionMode,
    SlashCommand,
    UnknownCommand,
    UserMessage,
)
from forgecli.infrastructure.project import (
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)
from forgecli.interfaces.cli import repl as repl_module
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import QuitSignal, Repl
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
        clock=lambda: "2026-06-27T10:00:00+08:00",
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


def _runtime() -> (
    tuple[
        Console, SessionState, CommandRegistry, RichOutput, IntentRouter, SessionService
    ]
):
    console = Console(record=True)
    state = SessionState()
    session = _session()
    output = RichOutput(console)
    registry = build_registry(
        session,
        _context(),
        _service(),
        RichMenuPresenter(console),
        _NullPicker(),
        output,
    )
    router = IntentRouter(registry)
    return console, state, registry, output, router, session


def _router() -> IntentRouter:
    return _runtime()[4]


def test_registry_contains_all_roadmap_slash_commands() -> None:
    registry = _runtime()[2]

    # 使用公开的 all_specs() 断言命令面，避免测试绑定 registry 内部存储结构。
    registered_names = {spec.name for spec in registry.all_specs()}
    assert registered_names >= {
        "help",
        "chat",
        "plan",
        "act",
        "status",
        "pause",
        "exit",
        "add-dir",
    }


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


@pytest.mark.parametrize("command", ["help", "status"])
def test_routes_known_slash_commands(command: str) -> None:
    router = _router()

    intent = router.route(f"/{command}")

    assert isinstance(intent, SlashCommand)
    assert intent.kind is IntentKind.SLASH_COMMAND
    assert intent.command == command
    assert intent.args == ()


def test_routes_hyphenated_slash_command() -> None:
    intent = _router().route("/add-dir")

    assert isinstance(intent, SlashCommand)
    assert intent.command == "add-dir"


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


def test_routes_mode_switch_commands() -> None:
    router = _router()

    assert router.route("/chat") == ModeChange(
        raw_text="/chat", target_mode=SessionMode.CHAT
    )
    assert router.route("/plan") == ModeChange(
        raw_text="/plan", target_mode=SessionMode.PLAN
    )
    assert router.route("/act") == ModeChange(
        raw_text="/act", target_mode=SessionMode.ACT
    )


def test_routes_unknown_slash_command_with_help_hint() -> None:
    intent = _router().route("/wat")

    assert isinstance(intent, UnknownCommand)
    assert intent.kind is IntentKind.UNKNOWN_COMMAND
    assert intent.command == "wat"
    assert "/help" in intent.error_message


def test_routes_exit_command_as_control_signal() -> None:
    intent = _router().route("/exit")

    assert isinstance(intent, ControlSignal)
    assert intent.kind is IntentKind.CONTROL
    assert intent.action is ControlAction.EXIT


def test_routes_pause_command_as_control_signal() -> None:
    intent = _router().route("/pause")

    assert isinstance(intent, ControlSignal)
    assert intent.kind is IntentKind.CONTROL
    assert intent.action is ControlAction.PAUSE


@pytest.mark.parametrize("raw_text", ["", "   ", "\n"])
def test_rejects_empty_input(raw_text: str) -> None:
    with pytest.raises(ValueError, match="raw_text 不能为空"):
        _router().route(raw_text)


def test_repl_process_line_routes_user_message() -> None:
    console, state, registry, output, router, session = _runtime()
    repl = Repl(console, router, registry, state, output, session)

    repl._process_line("解释这个项目")

    # 同屏回显用户输入(› 前缀)并给出助手那一轮(● 标记)；二者分色显示。
    rendered = console.export_text()
    assert "› 解释这个项目" in rendered
    assert "●" in rendered
    assert state.mode is SessionMode.CHAT
    assert state.should_exit is False


def test_repl_process_line_switches_mode_in_memory_only() -> None:
    console, state, registry, output, router, session = _runtime()
    repl = Repl(console, router, registry, state, output, session)

    repl._process_line("/plan")

    assert state.mode is SessionMode.PLAN
    assert state.should_exit is False
    assert "已切换到 plan 模式" in console.export_text()


def test_repl_process_line_dispatches_status_command() -> None:
    console, state, registry, output, router, session = _runtime()
    repl = Repl(console, router, registry, state, output, session)

    repl._process_line("/status")

    text = console.export_text()
    assert "mode:" in text
    assert "cwd:" in text
    assert state.should_exit is False


@pytest.mark.parametrize("line", ["/exit", "exit", "quit", ":q"])
def test_repl_process_line_exits_without_blocking(line: str) -> None:
    console, state, registry, output, router, session = _runtime()
    repl = Repl(console, router, registry, state, output, session)

    repl._process_line(line)

    assert state.should_exit is True


def test_repl_process_line_reports_unknown_command_with_help_hint() -> None:
    console, state, registry, output, router, session = _runtime()
    repl = Repl(console, router, registry, state, output, session)

    repl._process_line("/does-not-exist")

    text = console.export_text()
    assert "未知命令 /does-not-exist" in text
    assert "/help" in text
    assert state.should_exit is False


def test_repl_run_exits_on_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-D/EOF 应结束 REPL loop，且不需要真实终端输入。"""

    class EofPrompt:
        def __init__(self, commands: list[tuple[str, str]]) -> None:
            self.commands = commands

        def read(self) -> str:
            raise EOFError

    console, state, registry, output, router, session = _runtime()
    monkeypatch.setattr(repl_module, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(repl_module, "ForgePrompt", EofPrompt)

    Repl(console, router, registry, state, output, session).run()

    assert state.should_exit is False


def test_repl_run_exits_on_quit_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """空行连按两次 Ctrl-C 由 prompt 抛 QuitSignal，REPL 只负责安全退出。"""

    class QuitPrompt:
        def __init__(self, commands: list[tuple[str, str]]) -> None:
            self.commands = commands

        def read(self) -> str:
            raise QuitSignal

    console, state, registry, output, router, session = _runtime()
    monkeypatch.setattr(repl_module, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(repl_module, "ForgePrompt", QuitPrompt)

    Repl(console, router, registry, state, output, session).run()

    assert state.should_exit is False
