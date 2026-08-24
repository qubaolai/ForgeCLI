"""/exit 与 REPL 的退出路径 (ADR-0007 验收项).

关键不在"命令能抛异常", 而在**主循环真的会因此退出**: 原来 run() 的 try 只包住了
prompt.read(), 从分派里抛出来的退出信号会直接穿过循环变成 traceback.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from forgecli.application.intent_router import IntentRouter
from forgecli.application.slash_commands.registry import CommandRegistry, CommandSpec
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.interfaces.cli import repl as repl_module
from forgecli.interfaces.cli.commands.exit_command import ExitCommand
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import Repl
from forgecli.interfaces.cli.session_exit import SessionExit


class _Snapshot:
    mode = SessionMode.ACCEPT_EDITS
    session_id = "s1"


class _Session:
    def __init__(self) -> None:
        self.started = 0
        self.recorded: list[str] = []

    def start(self) -> _Snapshot:
        self.started += 1
        return _Snapshot()

    def current(self) -> _Snapshot:
        return _Snapshot()

    def record_slash_command(self, name: str, args: tuple[str, ...]) -> None:
        self.recorded.append(name)


class _Prompt:
    """脚本化输入框: 依次吐出预设的行, 用完抛 EOFError."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self.reads = 0

    def read(self) -> str:
        self.reads += 1
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


def _console() -> Console:
    return Console(file=io.StringIO(), width=80, no_color=True)


def _repl(lines: list[str], console: Console) -> tuple[Repl, _Prompt, _Session]:
    registry = CommandRegistry()
    output = RichOutput(console)
    registry.register(CommandSpec("exit", "退出会话", handler=ExitCommand(output)))
    session = _Session()
    prompt = _Prompt(lines)
    instance = Repl(
        console=console,
        router=IntentRouter(registry=registry),
        registry=registry,
        output=output,
        session=session,  # type: ignore[arg-type]
        agent_turn=None,  # type: ignore[arg-type]
        stream_view=None,  # type: ignore[arg-type]
        cancel_source=None,  # type: ignore[arg-type]
    )
    return instance, prompt, session


def test_the_command_signals_exit() -> None:
    with pytest.raises(SessionExit):
        ExitCommand(RichOutput(_console())).execute(SlashCommand("/exit", "exit"))


def test_the_main_loop_stops_on_a_dispatched_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/exit 从分派深处抛出, 循环仍要正常收尾而不是把 traceback 打到终端."""
    console = _console()
    instance, prompt, _ = _repl(["/exit", "这一行不该被读到"], console)
    monkeypatch.setattr(repl_module, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(repl_module, "ForgePrompt", lambda *a, **k: prompt)

    instance.run()

    assert prompt.reads == 1  # 读到 /exit 就停, 没有继续读下一行


def test_an_exhausted_input_stream_still_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EOF 兜底没有被新的 try 结构吃掉."""
    console = _console()
    instance, prompt, _ = _repl([], console)
    monkeypatch.setattr(repl_module, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(repl_module, "ForgePrompt", lambda *a, **k: prompt)

    instance.run()

    assert prompt.reads == 1


def test_exit_does_not_record_a_slash_command_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退出没有写任何持久状态, 不该在事件日志里留一条 slash_command."""
    console = _console()
    instance, prompt, session = _repl(["/exit"], console)
    monkeypatch.setattr(repl_module, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(repl_module, "ForgePrompt", lambda *a, **k: prompt)

    instance.run()

    assert session.recorded == []
