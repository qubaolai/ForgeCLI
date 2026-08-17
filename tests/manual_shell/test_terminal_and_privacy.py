"""终端恢复与隐私边界 (ADR-0017 §7, §9, §15.3, §15.4)."""

from __future__ import annotations

import dataclasses
import io

import pytest
from rich.console import Console

from forgecli.domain.manual_shell.request import ManualShellRequest, TerminalSize
from forgecli.domain.manual_shell.result import ManualShellResult
from forgecli.interfaces.cli import terminal_lease as lease_module
from forgecli.interfaces.cli.shell_mode import ConsoleManualShellObserver
from forgecli.interfaces.cli.terminal_lease import CliTerminalLease


def _console() -> Console:
    return Console(file=io.StringIO(), width=100, no_color=True)


def _text(console: Console) -> str:
    stream = console.file
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()


# ---- 终端租约 ----


class _Renderer:
    def __init__(self) -> None:
        self.finished = 0

    def finish(self) -> None:
        self.finished += 1


def test_the_live_region_is_collected_before_handing_over() -> None:
    """Live 与子 Shell 抢同一块屏幕, 不收会互相覆盖 (§7)."""
    renderer = _Renderer()
    with CliTerminalLease(_console(), renderer).acquire():  # type: ignore[arg-type]
        pass
    assert renderer.finished == 1


def test_the_lease_restores_the_terminal_even_when_the_shell_explodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复必须走 finally, 覆盖 Shell 崩溃与信号退出 (§7)."""
    restored: list[object] = []
    monkeypatch.setattr(lease_module, "_restore_terminal", restored.append)

    with pytest.raises(RuntimeError), CliTerminalLease(_console()).acquire():
        raise RuntimeError("shell 崩了")

    assert len(restored) == 1


def test_the_lease_prints_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """租约只管终端状态, 不打字.

    分隔空行原来在租约里, 已经挪到 ShellModeEntry 的会话分支 —— 留在这儿的话
    `$ clear` 刚清干净的屏幕会立刻被一个空行占上.
    """
    monkeypatch.setattr(lease_module, "_restore_terminal", lambda saved: None)
    console = _console()

    with CliTerminalLease(console).acquire():
        pass

    assert _text(console) == ""


def test_a_missing_renderer_is_fine() -> None:
    """人工 Shell 只在 turn 之间进入, 那时 Live 本来就没开."""
    with CliTerminalLease(_console()).acquire():
        pass


def test_no_terminal_attributes_does_not_block_entry() -> None:
    """pytest 捕获下 stdin 没有终端属性可保存. 那不该让人工 Shell 起不来 ——
    真正需要终端的是 Provider, 它自己会拒绝."""
    with CliTerminalLease(_console()).acquire():
        pass


# ---- 隐私: 结果里装不下 Shell 内容 ----


def test_the_result_has_no_field_for_shell_content() -> None:
    """§9 不是"暂时没做": ManualShellResult 故意只能装下这几个字段.

    想加一个 captured_output 进来, 得先改 ADR —— 这条测试会先拦一次.
    """
    fields = {field.name for field in dataclasses.fields(ManualShellResult)}
    assert fields == {
        "started_at",
        "finished_at",
        "exit_code",
        "signal",
        "start_error",
    }


def test_the_entry_banner_shows_no_environment_values() -> None:
    """进入提示只有 Shell 名与 cwd. 环境变量值里可能有 token (§9)."""
    console = _console()
    ConsoleManualShellObserver(console).entered(
        ManualShellRequest(
            shell_executable="/opt/homebrew/bin/zsh",
            argv=("/opt/homebrew/bin/zsh", "-i"),
            cwd="/ws/proj",
            environment={"AWS_SECRET_ACCESS_KEY": "leak-me", "PATH": "/usr/bin"},
            terminal=TerminalSize(120, 40),
        )
    )
    text = _text(console)

    assert "zsh" in text
    assert "/ws/proj" in text
    assert "leak-me" not in text
    assert "AWS_SECRET_ACCESS_KEY" not in text
    # 只显示 basename: 完整路径对用户没有增量信息, 却泄漏了安装布局.
    assert "/opt/homebrew/bin" not in text


def test_the_exit_summary_carries_only_the_outcome() -> None:
    result = ManualShellResult(started_at="t0", finished_at="t1", exit_code=0)
    assert result.summary == "返回 Forge · shell exit 0"


def test_a_start_failure_is_not_dressed_up_as_a_command_failure() -> None:
    """§12: 起不来是 Forge 的问题, 不是用户命令的问题."""
    result = ManualShellResult(
        started_at="t0", finished_at="t1", start_error="找不到 Shell"
    )
    assert not result.started
    assert "未能进入 Shell" in result.summary


def test_a_result_cannot_claim_both_failure_and_an_exit_code() -> None:
    with pytest.raises(ValueError, match="退出码"):
        ManualShellResult(
            started_at="t0", finished_at="t1", exit_code=0, start_error="x"
        )
