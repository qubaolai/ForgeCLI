"""CLI 入口的 smoke tests。

锁定当前 MVP 阶段最重要的交互契约：版本可查询、help 可用、裸 ``forge`` 能渲染
banner 并安全退出。非 TTY 下无法确认目录信任，应打印提示后干净退出（不进 REPL）。

用 FORGE_CONFIG_DIR 隔离 Forge home，确保测试不读用户真实 ~/.forge。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from forgecli.interfaces.cli.app import app
from forgecli.shared import __version__

# Typer 的测试 runner 会捕获 stdout/stderr，并模拟 stdin 输入。
runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_forge_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(tmp_path / "home"))


def test_version_option() -> None:
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert __version__ in res.stdout
    res = runner.invoke(app, ["-V"])
    assert res.exit_code == 0
    assert __version__ in res.stdout


def test_help_available_without_subcommands() -> None:
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "--version" in res.stdout
    assert "Forge" in res.stdout


def test_typer_subcommands_are_not_registered() -> None:
    for command in ("chat", "status"):
        result = runner.invoke(app, [command])
        assert result.exit_code == 2


def test_bare_forge_enters_and_exits_cleanly() -> None:
    # 非 TTY：渲染 banner 后因无法确认信任而干净退出。
    result = runner.invoke(app, [], input="exit\n")
    assert result.exit_code == 0


def test_bare_forge_exits_on_eof() -> None:
    # 不给任何输入也不应死循环或抛栈。
    result = runner.invoke(app, [], input="")
    assert result.exit_code == 0


def test_bare_forge_shows_banner() -> None:
    # 进入流程时应渲染 banner；用 tagline 关键字做稳定锚点。
    result = runner.invoke(app, [], input="exit\n")
    assert "锻造" in result.stdout
