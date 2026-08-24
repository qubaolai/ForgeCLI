"""`# 命令` 一次性执行 (ADR-0017 §3 的新增路由).

两件事各占一半:

- **路由**: 空格与单行两道限制决定了误伤范围 —— 前缀字符在 ONE_SHOT_PREFIX 一处定义.
- **界面**: 成功时一个字都不打, 否则 `# clear` 清出来的干净屏幕会立刻被"返回 Forge"占上.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from forgecli.application.intent_router import IntentRouter
from forgecli.application.manual_shell.provider import ManualShellContext
from forgecli.domain.intents import InputOrigin, ManualShellIntent, UserMessage
from forgecli.domain.manual_shell.request import ManualShellRequest
from forgecli.domain.manual_shell.result import ManualShellResult
from forgecli.infrastructure.manual_shell.shell_selection import SystemShellResolver
from forgecli.interfaces.cli.shell_mode import (
    ConsoleManualShellObserver,
    ShellModeEntry,
)
from support.fakes import PROFILE

# ---- 路由 ----


def _route(text: str, origin: InputOrigin = InputOrigin.TTY_USER) -> object:
    from forgecli.application.slash_commands.registry import CommandRegistry

    return IntentRouter(registry=CommandRegistry()).route(text, origin=origin)


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("# clear", "clear"),
        ("# git status", "git status"),
        ("#  ls -la  ", "ls -la"),
        ("# npm install -g foo", "npm install -g foo"),
    ],
)
def test_a_hash_prefix_runs_one_command(text: str, command: str) -> None:
    intent = _route(text)
    assert isinstance(intent, ManualShellIntent)
    assert intent.command == command
    assert not intent.interactive


@pytest.mark.parametrize(
    "text",
    ["#!/bin/sh", "#include <stdio.h>", "#define X 1", "#123", "## 二级标题", "#tag"],
)
def test_text_without_a_space_after_the_hash_is_not_a_command(text: str) -> None:
    """要求 `#` 后面有空格, 挡的就是这一类. 它们是 shebang, 预处理指令, issue 号与
    markdown 层级 —— 都不是 `"# "` 开头, 全部照常发给模型."""
    assert isinstance(_route(text), UserMessage)


def test_multiline_input_is_never_a_command() -> None:
    """单行限制专门保住"粘贴一篇 markdown"这个场景."""
    assert isinstance(_route("# 标题\n\n正文在这里"), UserMessage)


def test_a_hash_command_from_a_program_is_just_text() -> None:
    """模型输出里出现 `# rm -rf /` 不该变成一条真命令."""
    assert isinstance(_route("# rm -rf /", InputOrigin.PROGRAM), UserMessage)


def test_a_bare_hash_still_opens_a_session() -> None:
    intent = _route("#")
    assert isinstance(intent, ManualShellIntent)
    assert intent.interactive


def test_a_single_line_heading_is_the_accepted_casualty() -> None:
    """这条**不是**在断言正确行为, 是在把已知代价钉住.

    单行 `# 标题` 会被当成命令跑掉 —— 这是选 `#` 而不是 `$` 买下的唯一误伤, 也是
    ADR-0017 决策 1 被修订的那一处. 兜底是 127 提示 (见下面 not_found 那条).
    有人想改回"标题也是自然语言"时, 会先撞到这条测试.
    """
    intent = _route("# 标题")
    assert isinstance(intent, ManualShellIntent)
    assert intent.command == "标题"


# ---- 启动参数 ----


def _resolve(command: str) -> ManualShellRequest:
    return SystemShellResolver(PROFILE).resolve(
        ManualShellContext(
            cwd="/ws", environment={"SHELL": "/bin/zsh"}, command=command
        )
    )


def test_a_one_shot_keeps_the_interactive_flag() -> None:
    """`-i -c` 而不是 `-c`: 没有 `-i` 就不读 rc, 用户的 alias 和函数全都不认识,
    而 `$ ll` 正是最常见的用法之一."""
    request = _resolve("ll")
    assert request.argv[1:] == ("-i", "-c", "ll")
    assert not request.interactive


def test_a_session_takes_no_command() -> None:
    request = _resolve("")
    assert request.argv[1:] == ("-i",)
    assert request.interactive


@pytest.mark.parametrize("command", ["", "clear"])
def test_the_shell_is_marked_as_running_under_forge(command: str) -> None:
    """用户在 shell 里分不清自己是不是从 Forge 进来的.

    用环境变量而不是改 prompt: ADR-0017 §4 要求"Forge 不伪造 Shell prompt", 而往别人的
    PROMPT 里插东西会打坏 powerlevel10k / starship 这类自己拼提示符的配置. 变量是这类
    "我在某个子环境里"的通行做法 (VIRTUAL_ENV, IN_NIX_SHELL, TMUX), 怎么显示由用户定.
    """
    request = _resolve(command)
    assert request.environment["FORGE_SHELL"] == "1"
    assert request.environment["FORGE_SHELL_CWD"] == "/ws"


# ---- 界面 ----


class _Service:
    def __init__(self, result: ManualShellResult) -> None:
        self._result = result
        self.seen: ManualShellContext | None = None
        self.barrier = _Barrier()

    def enter(
        self, intent: ManualShellIntent, context: ManualShellContext
    ) -> ManualShellResult:
        self.seen = context
        return self._result


class _Barrier:
    blocked = False
    block_reason = ""


def _run(result: ManualShellResult, command: str) -> str:
    console = Console(file=io.StringIO(), width=100, no_color=True)
    ShellModeEntry(
        console,
        _Service(result),  # type: ignore[arg-type]
        lambda: ManualShellContext(cwd="/ws"),
    ).enter(ManualShellIntent(raw_text=f"# {command}", command=command))
    stream = console.file
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()


def _ok(code: int = 0) -> ManualShellResult:
    return ManualShellResult(started_at="t0", finished_at="t1", exit_code=code)


def test_a_successful_one_shot_prints_nothing() -> None:
    """这条是 `# clear` 能用的全部前提: 多打一个字符, 屏幕就不干净了."""
    assert _run(_ok(), "clear") == ""


def test_a_nonzero_exit_is_reported_plainly() -> None:
    text = _run(_ok(2), "grep nope file")
    assert "退出码 2" in text


def test_command_not_found_hints_at_the_collision() -> None:
    """127 是"命令没找到"的 POSIX 约定. 只看退出码, 不看输出 —— §9 不许捕获输出."""
    text = _run(_ok(127), "这句其实是想说给模型听的")
    assert "命令未找到" in text
    assert '"# "' in text


def test_a_signal_kill_is_not_called_an_exit_code() -> None:
    text = _run(
        ManualShellResult(started_at="t0", finished_at="t1", signal=9), "sleep 100"
    )
    assert "信号 9" in text
    assert "退出码" not in text


def test_a_start_failure_still_speaks_up() -> None:
    text = _run(
        ManualShellResult(
            started_at="t0", finished_at="t1", start_error="找不到 Shell"
        ),
        "clear",
    )
    assert "未能进入 Shell" in text


def test_the_command_reaches_the_context() -> None:
    service = _Service(_ok())
    console = Console(file=io.StringIO(), width=100, no_color=True)
    ShellModeEntry(
        console,
        service,  # type: ignore[arg-type]
        lambda: ManualShellContext(cwd="/ws"),
    ).enter(ManualShellIntent(raw_text="# ls", command="ls"))

    assert service.seen is not None
    assert service.seen.command == "ls"


def test_the_entry_banner_is_skipped_for_one_shots() -> None:
    """`# clear` 前面来一句"进入 Shell 模式"同样毁掉干净屏幕."""
    console = Console(file=io.StringIO(), width=100, no_color=True)
    ConsoleManualShellObserver(console).entered(
        ManualShellRequest(
            shell_executable="/bin/zsh",
            argv=("/bin/zsh", "-i", "-c", "clear"),
            cwd="/ws",
            command="clear",
        )
    )
    stream = console.file
    assert isinstance(stream, io.StringIO)
    assert stream.getvalue() == ""


def test_a_bad_shell_variable_falls_back_instead_of_failing() -> None:
    """$SHELL 指向不存在的文件时退到执行画像探到的 Shell (§6.1 的第二顺位),
    而不是让用户敲个 `# clear` 就撞一个错误."""
    request = SystemShellResolver(PROFILE).resolve(
        ManualShellContext(
            cwd="/ws", environment={"SHELL": "/nope/zsh"}, command="clear"
        )
    )
    assert request.shell_executable == PROFILE.shell_launch.program
