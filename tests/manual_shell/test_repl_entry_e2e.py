"""从 REPL 输入 `#` 到真实 Shell 跑起来 (ADR-0017 §16 首条验收标准).

Provider 自己的 pty 测试证明"能起 Shell"; 这条证明**整条链路是通的**:

    prompt.read() -> IntentRouter(origin=TTY_USER) -> ManualShellIntent
      -> Repl._dispatch -> ShellModeEntry -> ManualShellService
      -> TerminalLease -> Provider -> 真 /bin/sh

中间少接一根线, 用户敲 `#` 就会变成一句发给模型的自然语言 —— 那种失败在单元测试里
看不出来, 因为每一段自己都是对的.
"""

from __future__ import annotations

import os
import pty
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":  # pragma: no cover
    pytest.skip("POSIX 专属", allow_module_level=True)

_SH = "/bin/sh"
pytestmark = pytest.mark.skipif(
    not os.path.exists(_SH), reason=f"没有 {_SH}, 无法跑真实交互式 Shell"
)

_MARKER = "forge-repl-shell-ok"

_PROBE = '''
import io, os, sys
sys.path.insert(0, {src!r})
from rich.console import Console

from forgecli.application.intent_router import IntentRouter
from forgecli.application.manual_shell import ManualShellContext, ManualShellService
from forgecli.application.slash_commands import CommandRegistry
from forgecli.domain.intents import SessionMode
from forgecli.domain.manual_shell.request import ManualShellRequest
from forgecli.application.manual_shell import InteractiveShellResolver
from forgecli.infrastructure.manual_shell import build_interactive_shell_provider
from forgecli.interfaces.cli import repl as repl_module
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import Repl
from forgecli.interfaces.cli.shell_mode import (
    ConsoleManualShellObserver,
    ShellModeEntry,
)
from forgecli.interfaces.cli.terminal_lease import CliTerminalLease


class Resolver(InteractiveShellResolver):
    """固定跑一句 echo 就退出 —— 交互式 Shell 会等输入, 而测试里没有人来敲."""

    def resolve(self, context):
        return ManualShellRequest(
            shell_executable={sh!r},
            argv=({sh!r}, "-c", "echo {marker}"),
            cwd=context.cwd,
            environment=dict(os.environ),
        )


class Snapshot:
    mode = SessionMode.ACCEPT_EDITS
    session_id = "s1"


class Session:
    def start(self): return Snapshot()
    def current(self): return Snapshot()
    def record_slash_command(self, name, args): pass


class Prompt:
    """脚本化输入框: 先给一个 #, 再给 EOF."""

    def __init__(self):
        self.lines = ["#"]

    def read(self):
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


console = Console(force_terminal=True)
service = ManualShellService(
    Resolver(),
    build_interactive_shell_provider(),
    CliTerminalLease(console),
    ConsoleManualShellObserver(console),
)
registry = CommandRegistry()
prompt = Prompt()
repl_module.ForgePrompt = lambda *a, **k: prompt

instance = Repl(
    console=console,
    router=IntentRouter(registry=registry),
    registry=registry,
    output=RichOutput(console),
    session=Session(),
    agent_turn=None,
    stream_view=None,
    cancel_source=None,
    shell_mode=ShellModeEntry(
        console, service, lambda: ManualShellContext(cwd=os.getcwd())
    ),
)
instance.run()
sys.exit(0 if service.barrier.generation == 1 else 31)
'''


def _run_in_pty(source: str) -> tuple[int, str]:
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execv(sys.executable, [sys.executable, "-c", source])
        finally:  # pragma: no cover
            os._exit(99)
    output = bytearray()
    try:
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
    finally:
        os.close(master)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), output.decode("utf-8", "replace")


def test_a_bare_hash_at_the_prompt_reaches_a_real_shell() -> None:
    source = _PROBE.format(
        src=str(Path(__file__).resolve().parents[2] / "src"),
        sh=_SH,
        marker=_MARKER,
    )
    code, output = _run_in_pty(source)

    assert code == 0, f"退出码 {code}, 终端输出:\n{output}"
    # 边界提示 + Shell 真的跑了 + 返回摘要, 三样都要在.
    assert "进入 Shell 模式" in output
    assert _MARKER in output
    assert "返回 Forge" in output


def test_the_barrier_trips_after_returning_from_the_repl() -> None:
    """探针的退出码就是这条断言: barrier.generation 必须是 1 (§10)."""
    source = _PROBE.format(
        src=str(Path(__file__).resolve().parents[2] / "src"),
        sh=_SH,
        marker=_MARKER,
    )
    code, output = _run_in_pty(source)
    assert code != 31, f"从 REPL 回来没有触发失效屏障, 终端输出:\n{output}"
