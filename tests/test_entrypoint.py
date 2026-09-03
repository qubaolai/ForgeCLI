"""入口分发的判据 (ADR-0025 决策 1 / ADR-0045).

这一组存在的直接原因是曾经发生过的一次回归: 终端入口整棵代码树完好, 只是没有任何
调用方 —— 代码能跑, 测试全绿, 而那个入口已经死了. 入口分发因此要有自己的用例, 而不是
指望别的用例顺带覆盖到.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from forgecli.interfaces import app as entrypoint
from forgecli.interfaces.exit_codes import ExitCode

runner = CliRunner()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """把两条启动路径都换成记录仪: 用例不该真的起服务或读终端."""
    seen: dict[str, object] = {}

    def fake_web(*, port: int, open_browser: bool) -> int:
        seen["web"] = {"port": port, "open_browser": open_browser}
        return 0

    def fake_cli() -> int:
        seen["cli"] = True
        return 0

    monkeypatch.setattr(entrypoint, "run_web", fake_web)
    monkeypatch.setattr(entrypoint, "run_cli", fake_cli)
    return seen


def test_bare_forge_starts_the_web_control_plane(calls: dict[str, object]) -> None:
    result = runner.invoke(entrypoint.app, [])
    assert result.exit_code == 0
    assert calls == {"web": {"port": 8765, "open_browser": False}}


def test_web_arguments_are_passed_through(calls: dict[str, object]) -> None:
    result = runner.invoke(entrypoint.app, ["--port", "8899", "--open"])
    assert result.exit_code == 0
    assert calls == {"web": {"port": 8899, "open_browser": True}}


def test_cli_flag_starts_the_terminal_session(calls: dict[str, object]) -> None:
    result = runner.invoke(entrypoint.app, ["--cli"])
    assert result.exit_code == 0
    assert calls == {"cli": True}


def test_cli_refuses_web_only_arguments(calls: dict[str, object]) -> None:
    """静默忽略会让人以为 `forge --cli --port 9000` 起了一个监听 9000 的什么东西."""
    result = runner.invoke(entrypoint.app, ["--cli", "--port", "9000"])
    assert result.exit_code == 2
    assert calls == {}


def test_exit_codes_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "这一次没能开始"必须让脚本判得出来, 不能被入口吞成 0."""
    monkeypatch.setattr(entrypoint, "run_cli", lambda: ExitCode.PROJECT_LOCKED)
    monkeypatch.setattr(
        entrypoint, "run_web", lambda *, port, open_browser: ExitCode.PORT_BUSY
    )
    assert runner.invoke(entrypoint.app, ["--cli"]).exit_code == ExitCode.PROJECT_LOCKED
    assert runner.invoke(entrypoint.app, []).exit_code == ExitCode.PORT_BUSY


def test_version_starts_nothing(calls: dict[str, object]) -> None:
    result = runner.invoke(entrypoint.app, ["--version"])
    assert result.exit_code == 0
    assert calls == {}


def test_there_is_no_cli_subcommand(calls: dict[str, object]) -> None:
    """入口的分叉是一个参数, 不是子命令.

    `forge cli` 曾经存在过又被删掉 (ADR-0025 决策 1 修订二). 一个被删掉的入口最容易
    以"顺手加回来"的形式复活, 而复活的那一刻不会有任何东西报错.
    """
    assert runner.invoke(entrypoint.app, ["cli"]).exit_code != 0
    assert calls == {}
