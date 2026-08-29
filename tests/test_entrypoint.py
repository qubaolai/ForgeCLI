"""唯一启动路径的入口分发 (ADR-0025 决策 1 + 2026-08-29 修订二).

裸 `forge` 起 Web 控制面, 没有别的子命令. 这一组用例存在的理由很具体: 这条路径不在
单元测试的常规覆盖里 (它要监听端口), 而"入口悄悄变了"是一次静默的产品回归 ——
2026-08-20 加 Web 支持时终端入口就是这么消失的: 代码完好, 只是没人再调它.

所以这里只钉四件事: 谁被调了, 参数传对没有, 退出码有没有原样传出去, 以及那条已经
删掉的终端子命令没有偷偷回来.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from forgecli.interfaces import app as app_module
from forgecli.interfaces import exit_codes
from forgecli.interfaces.exit_codes import ExitCode

runner = CliRunner()


def test_bare_forge_starts_the_web_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        app_module, "run_web", lambda **kwargs: calls.append(kwargs) or ExitCode.OK
    )

    result = runner.invoke(app_module.app, [])

    assert result.exit_code == 0
    assert calls == [{"port": 8765, "open_browser": False}]


def test_the_web_options_reach_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        app_module, "run_web", lambda **kwargs: calls.append(kwargs) or ExitCode.OK
    )

    result = runner.invoke(app_module.app, ["--port", "9123", "--open"])

    assert result.exit_code == 0
    assert calls == [{"port": 9123, "open_browser": True}]


def test_a_locked_project_propagates_its_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退出码是脚本与 CI 的判据, 不能被入口层吞成 0 或 1."""
    monkeypatch.setattr(app_module, "run_web", lambda **_: ExitCode.PROJECT_LOCKED)

    assert runner.invoke(app_module.app, []).exit_code == 6


def test_version_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "run_web", _must_not_run)

    result = runner.invoke(app_module.app, ["--version"])

    assert result.exit_code == 0
    assert "forge" in result.stdout


def test_there_is_no_terminal_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    """`forge cli` 由 ADR-0025 修订二删除.

    钉住它而不是只删代码: 一个被删掉的入口最容易以"顺手加回来"的形式复活, 而复活的
    那一刻不会有任何东西报错 —— 它会安静地变回第二套业务入口.
    """
    monkeypatch.setattr(app_module, "run_web", _must_not_run)

    result = runner.invoke(app_module.app, ["cli"])

    assert result.exit_code != 0


def test_the_exit_code_table_has_no_duplicates() -> None:
    """一个退出码只对应一个原因 —— 这是 exit_codes 模块的全部契约."""
    values = [member.value for member in ExitCode]
    assert len(values) == len(set(values))
    assert exit_codes.ExitCode.PROJECT_LOCKED == 6


def _must_not_run(*args: object, **kwargs: object) -> None:
    raise AssertionError("这条启动路径不该被走到")
