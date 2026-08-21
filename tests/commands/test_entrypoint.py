"""两条启动路径的入口分发 (ADR-0025 决策 1 + 2026-08-21 修订).

裸 `forge` 起 Web 控制面, `forge cli` 进终端会话. 这一组用例存在的理由很具体: 这两条
路径都不在单元测试的常规覆盖里 (一条要监听端口, 一条要真终端), 而"某条路径没人调了"
是一次静默的产品回归 —— 2026-08-20 加 Web 支持时, 终端入口就是这么消失的, 代码完好,
只是没人再调 `bootstrap.run()`.

所以这里只钉三件事: 谁被调了, 参数传对没有, 退出码有没有原样传出去.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from forgecli.interfaces import exit_codes
from forgecli.interfaces.cli import app as app_module
from forgecli.interfaces.exit_codes import ExitCode

runner = CliRunner()


def test_bare_forge_starts_the_web_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        app_module, "run_web", lambda **kwargs: calls.append(kwargs) or ExitCode.OK
    )
    monkeypatch.setattr(app_module, "run_session", _must_not_run)

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


def test_forge_cli_enters_the_terminal_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        app_module, "run_session", lambda: calls.append(1) or ExitCode.OK
    )
    # 裸 forge 的那条路径一步都不能走: 两条入口共用一把项目锁, 顺手起个服务会让
    # 终端会话拿不到锁.
    monkeypatch.setattr(app_module, "run_web", _must_not_run)

    result = runner.invoke(app_module.app, ["cli"])

    assert result.exit_code == 0
    assert calls == [1]


def test_a_refused_terminal_session_propagates_its_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退出码是脚本与 CI 的判据, 不能被入口层吞成 0 或 1."""
    monkeypatch.setattr(app_module, "run_session", lambda: ExitCode.NO_TTY)

    result = runner.invoke(app_module.app, ["cli"])

    assert result.exit_code == int(ExitCode.NO_TTY) == 4


def test_a_locked_project_uses_the_same_code_on_both_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两条路径取的是同一把 forge.lock, 所以"已被占用"不该有两个码."""
    monkeypatch.setattr(app_module, "run_session", lambda: ExitCode.PROJECT_LOCKED)
    monkeypatch.setattr(app_module, "run_web", lambda **_: ExitCode.PROJECT_LOCKED)

    assert runner.invoke(app_module.app, ["cli"]).exit_code == 6
    assert runner.invoke(app_module.app, []).exit_code == 6


def test_version_starts_neither_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "run_web", _must_not_run)
    monkeypatch.setattr(app_module, "run_session", _must_not_run)

    result = runner.invoke(app_module.app, ["--version"])

    assert result.exit_code == 0
    assert "forge" in result.stdout


def test_the_exit_code_table_has_no_duplicates() -> None:
    """一个退出码只对应一个原因 —— 这是 exit_codes 模块的全部契约."""
    values = [member.value for member in ExitCode]
    assert len(values) == len(set(values))
    assert exit_codes.ExitCode.PROJECT_LOCKED == 6


def _must_not_run(*args: object, **kwargs: object) -> None:
    raise AssertionError("这条启动路径不该被走到")
