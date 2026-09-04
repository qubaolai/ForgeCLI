"""入口分发的判据 (ADR-0025 决策 1 / ADR-0045).

这一组存在的直接原因是曾经发生过的一次回归: 终端入口整棵代码树完好, 只是没有任何
调用方 —— 代码能跑, 测试全绿, 而那个入口已经死了. 入口分发因此要有自己的用例, 而不是
指望别的用例顺带覆盖到.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from forgecli.interfaces import app as entrypoint
from forgecli.interfaces.exit_codes import ExitCode

runner = CliRunner()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """把两条启动路径都换成记录仪: 用例不该真的起服务或读终端."""
    seen: dict[str, object] = {}

    def fake_web(*, port: int, open_browser: bool, mock_llm: Path | None) -> int:
        seen["web"] = {
            "port": port,
            "open_browser": open_browser,
            "mock_llm": mock_llm,
        }
        return 0

    def fake_cli(*, mock_llm: Path | None) -> int:
        seen["cli"] = {"mock_llm": mock_llm}
        return 0

    monkeypatch.setattr(entrypoint, "run_web", fake_web)
    monkeypatch.setattr(entrypoint, "run_cli", fake_cli)
    return seen


def test_bare_forge_starts_the_web_control_plane(calls: dict[str, object]) -> None:
    result = runner.invoke(entrypoint.app, [])
    assert result.exit_code == 0
    assert calls == {"web": {"port": 8765, "open_browser": False, "mock_llm": None}}


def test_web_arguments_are_passed_through(calls: dict[str, object]) -> None:
    result = runner.invoke(entrypoint.app, ["--port", "8899", "--open"])
    assert result.exit_code == 0
    assert calls == {"web": {"port": 8899, "open_browser": True, "mock_llm": None}}


def test_cli_flag_starts_the_terminal_session(calls: dict[str, object]) -> None:
    result = runner.invoke(entrypoint.app, ["--cli"])
    assert result.exit_code == 0
    assert calls == {"cli": {"mock_llm": None}}


def test_cli_refuses_web_only_arguments(calls: dict[str, object]) -> None:
    """静默忽略会让人以为 `forge --cli --port 9000` 起了一个监听 9000 的什么东西."""
    result = runner.invoke(entrypoint.app, ["--cli", "--port", "9000"])
    assert result.exit_code == 2
    assert calls == {}


def test_exit_codes_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "这一次没能开始"必须让脚本判得出来, 不能被入口吞成 0."""
    monkeypatch.setattr(
        entrypoint, "run_cli", lambda *, mock_llm: ExitCode.PROJECT_LOCKED
    )
    monkeypatch.setattr(
        entrypoint,
        "run_web",
        lambda *, port, open_browser, mock_llm: ExitCode.PORT_BUSY,
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


def test_bare_mock_llm_uses_the_script_in_forge_home(
    calls: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """路径可省: "我先随便试试假模型"不该先要求人想好把文件放哪."""
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(entrypoint.app, ["--mock-llm"])
    assert result.exit_code == 0
    script = tmp_path / entrypoint.MOCK_SCRIPT_NAME
    assert script.exists(), "文件不存在时要先写一份示例"
    assert calls == {"web": {"port": 8765, "open_browser": False, "mock_llm": script}}


def test_the_flag_works_in_any_argument_order(
    calls: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开关与路径分成两个选项, 正是为了买下这条.

    合成一个"路径可省"的选项的话, `--mock-llm --port 8801` 会把 `--port` 当成值吃掉,
    而一个在参数顺序变了之后就换行为的开关, 不值得省下一个选项名.
    """
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(tmp_path))
    assert (
        runner.invoke(entrypoint.app, ["--mock-llm", "--port", "8801"]).exit_code == 0
    )
    assert (
        runner.invoke(entrypoint.app, ["--port", "8801", "--mock-llm"]).exit_code == 0
    )
    assert calls["web"] == {
        "port": 8801,
        "open_browser": False,
        "mock_llm": tmp_path / entrypoint.MOCK_SCRIPT_NAME,
    }


def test_a_script_path_implies_the_flag(
    calls: dict[str, object], tmp_path: Path
) -> None:
    """给了路径就等于开了假模型: 让人两个都写一遍只是在惩罚他多打一次."""
    script = tmp_path / "mock.json"
    result = runner.invoke(entrypoint.app, ["--mock-llm-script", str(script)])
    assert result.exit_code == 0
    assert script.exists()
    assert calls == {"web": {"port": 8765, "open_browser": False, "mock_llm": script}}


def test_mock_llm_is_announced_loudly(calls: dict[str, object], tmp_path: Path) -> None:
    """一段编出来的回答与真模型的回答长得一模一样, 不喊一声没人分得清."""
    result = runner.invoke(
        entrypoint.app, ["--mock-llm-script", str(tmp_path / "m.json")]
    )
    assert "假模型已开启" in result.output


def test_mock_llm_reaches_the_terminal_entry_too(
    calls: dict[str, object], tmp_path: Path
) -> None:
    script = tmp_path / "mock.json"
    result = runner.invoke(entrypoint.app, ["--cli", "--mock-llm-script", str(script)])
    assert result.exit_code == 0
    assert calls == {"cli": {"mock_llm": script}}
