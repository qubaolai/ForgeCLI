"""CLI 入口的 smoke tests。

这些测试先锁定 MVP 阶段最重要的交互契约：版本可查询、help 可用、占位命令
可执行、裸 ``forge`` 能进入并安全退出 REPL。
"""

from __future__ import annotations

from typer.testing import CliRunner

from forgecli.interfaces.cli.app import app
from forgecli.shared import __version__

# Typer 的测试 runner 会捕获 stdout/stderr，并模拟 stdin 输入。
runner = CliRunner()


def test_version_option() -> None:
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert __version__ in res.stdout
    res = runner.invoke(app, ["-V"])
    assert res.exit_code == 0
    assert __version__ in res.stdout


def test_help_lists_commands() -> None:
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    # --help 应列出两个占位子命令，确保后续重构没有误删 CLI surface。
    assert "chat" in res.stdout
    assert "status" in res.stdout


def test_chat_placeholder_runs() -> None:
    result = runner.invoke(app, ["chat"])
    assert result.exit_code == 0


def test_status_placeholder_runs() -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0


def test_bare_forge_enters_and_exits_repl() -> None:
    # 模拟用户输入 "exit"：REPL 应正常进入并退出。
    result = runner.invoke(app, [], input="exit\n")
    assert result.exit_code == 0


def test_bare_forge_exits_on_eof() -> None:
    # 不给任何输入时会触发 EOF（等价 Ctrl-D），REPL 不应死循环或抛栈。
    result = runner.invoke(app, [], input="")
    assert result.exit_code == 0


def test_repl_shows_banner() -> None:
    # 进入 REPL 时应渲染 banner；用 tagline 关键字做稳定锚点。
    result = runner.invoke(app, [], input="exit\n")
    assert "锻造" in result.stdout
