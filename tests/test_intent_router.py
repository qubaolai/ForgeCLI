"""交互输入与斜杠命令路由的验收测试。

测试分三层：
    1. registry 是否注册了 roadmap 要求的命令面。
    2. IntentRouter 是否把单行输入解析为稳定的领域 intent。
    3. REPL stub 是否复用 IntentRouter，并把一轮对话交给 AgentTurnService、安全退出。
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from rich.console import Console

from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.intent_router import IntentRouter
from forgecli.application.interaction_ports import DirectoryPicker
from forgecli.application.project import (
    ProjectConfig,
    ProjectContext,
    ProjectService,
)
from forgecli.application.session import SessionService
from forgecli.application.slash_commands import CommandRegistry
from forgecli.domain.intents import (
    IntentKind,
    SessionMode,
    SlashCommand,
    UnknownCommand,
    UserMessage,
)
from forgecli.infrastructure.project import (
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore
from forgecli.interfaces.cli import repl as repl_module
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import QuitSignal, Repl
from forgecli.interfaces.cli.wiring import build_registry


class _NullPicker(DirectoryPicker):
    def pick(self, list_subdirs: Callable[[str], Sequence[str]]) -> str | None:
        return None


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


def _session() -> SessionService:
    sessions = Path(tempfile.mkdtemp()) / "sessions"
    service = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
    )
    service.start()
    return service


def _runtime() -> (
    tuple[
        Console,
        CommandRegistry,
        RichOutput,
        IntentRouter,
        SessionService,
        AgentTurnService,
    ]
):
    console = Console(record=True)
    output = RichOutput(console)
    session = _session()
    agent_turn = AgentTurnService(session)
    registry = build_registry(
        session,
        _context(),
        _service(),
        RichMenuPresenter(console),
        _NullPicker(),
        output,
        agent_turn,
    )
    router = IntentRouter(registry)
    return console, registry, output, router, session, agent_turn


def _router() -> IntentRouter:
    return _runtime()[3]


def test_registry_contains_all_roadmap_slash_commands() -> None:
    registry = _runtime()[1]

    # 使用公开的 all_specs() 断言命令面，避免测试绑定 registry 内部存储结构。
    # /exit、/pause 已移除：退出走键盘信号，暂停留待后续实现。
    registered_names = {spec.name for spec in registry.all_specs()}
    assert registered_names >= {
        "help",
        "chat",
        "plan",
        "act",
        "status",
        "add-dir",
        "resume",
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


@pytest.mark.parametrize("command", ["chat", "plan", "act"])
def test_routes_mode_switch_as_plain_slash_command(command: str) -> None:
    # 模式切换不再是单独意图，统一解析为 SlashCommand，差异落在 handler。
    intent = _router().route(f"/{command}")

    assert isinstance(intent, SlashCommand)
    assert intent.kind is IntentKind.SLASH_COMMAND
    assert intent.command == command


def test_routes_unknown_slash_command_with_help_hint() -> None:
    intent = _router().route("/wat")

    assert isinstance(intent, UnknownCommand)
    assert intent.kind is IntentKind.UNKNOWN_COMMAND
    assert intent.command == "wat"
    assert "/help" in intent.error_message


@pytest.mark.parametrize("removed", ["exit", "pause"])
def test_routes_removed_control_commands_as_unknown(removed: str) -> None:
    # /exit、/pause 已注销：现在它们只是未注册命令，给出 /help 提示。
    intent = _router().route(f"/{removed}")

    assert isinstance(intent, UnknownCommand)
    assert intent.command == removed


@pytest.mark.parametrize("raw_text", ["", "   ", "\n"])
def test_rejects_empty_input(raw_text: str) -> None:
    with pytest.raises(ValueError, match="raw_text 不能为空"):
        _router().route(raw_text)


def test_repl_process_line_routes_user_message() -> None:
    console, registry, output, router, session, agent_turn = _runtime()
    repl = Repl(console, router, registry, output, session, agent_turn)

    repl._process_line("解释这个项目")

    # 同屏回显用户输入(› 前缀)并给出助手那一轮(● 标记)；二者分色显示。
    rendered = console.export_text()
    assert "› 解释这个项目" in rendered
    assert "●" in rendered
    assert session.current().mode is SessionMode.CHAT


def test_repl_process_line_switches_mode_via_session() -> None:
    console, registry, output, router, session, agent_turn = _runtime()
    repl = Repl(console, router, registry, output, session, agent_turn)

    repl._process_line("/plan")

    # mode 单一真相在 session 快照（REPL 不再持有 mode）。
    assert session.current().mode is SessionMode.PLAN
    assert "已切换到 plan 模式" in console.export_text()


def test_repl_process_line_dispatches_status_command() -> None:
    console, registry, output, router, session, agent_turn = _runtime()
    repl = Repl(console, router, registry, output, session, agent_turn)

    repl._process_line("/status")

    text = console.export_text()
    assert "mode:" in text
    assert "cwd:" in text


def test_repl_process_line_reports_unknown_command_with_help_hint() -> None:
    console, registry, output, router, session, agent_turn = _runtime()
    repl = Repl(console, router, registry, output, session, agent_turn)

    repl._process_line("/does-not-exist")

    text = console.export_text()
    assert "未知命令 /does-not-exist" in text
    assert "/help" in text


def test_repl_run_exits_on_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-D/EOF 应结束 REPL loop，且不需要真实终端输入。"""

    class EofPrompt:
        def __init__(self, commands: list[tuple[str, str]]) -> None:
            self.commands = commands

        def read(self) -> str:
            raise EOFError

    console, registry, output, router, session, agent_turn = _runtime()
    monkeypatch.setattr(repl_module, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(repl_module, "ForgePrompt", EofPrompt)

    # 不挂起、不抛异常地正常返回即为通过。
    Repl(console, router, registry, output, session, agent_turn).run()


def test_repl_run_exits_on_quit_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """空行连按两次 Ctrl-C 由 prompt 抛 QuitSignal，REPL 只负责安全退出。"""

    class QuitPrompt:
        def __init__(self, commands: list[tuple[str, str]]) -> None:
            self.commands = commands

        def read(self) -> str:
            raise QuitSignal

    console, registry, output, router, session, agent_turn = _runtime()
    monkeypatch.setattr(repl_module, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(repl_module, "ForgePrompt", QuitPrompt)

    Repl(console, router, registry, output, session, agent_turn).run()
