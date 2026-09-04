"""终端审批卡片的判据.

画的是与 Web 同一份 `ApprovalView.to_payload()`. 少画一样东西不会有任何东西报错,
只会让用户在终端上批准一次他没读全的调用.
"""

from __future__ import annotations

import builtins
import io
from collections.abc import Iterator

import pytest
from rich.console import Console

from forgecli.interfaces.tui.approval_prompt import ask_decision, render_card

_VIEW: dict[str, object] = {
    "mode": "workspace_write/always",
    "tool_name": "shell_run",
    "workspace_roots": ["/repo"],
    "raw_command": "rm -rf build",
    "target_resolution": "closed",
    "target_groups": [{"label": "删除", "paths": ["build/a", "build/b"]}],
    "script_snapshots": [
        {"language": "bash", "origin": "inline", "path": "", "source": "rm -rf build"}
    ],
    "content_previews": [],
    "unresolved_reason": None,
    "allowed_scopes": ["once", "workspace"],
    "learn_blocked_reason": "",
    "counts": [{"label": "删除", "count": 2}],
}


@pytest.fixture
def console(screen: io.StringIO) -> Console:
    return Console(file=screen, width=100, no_color=True, highlight=False)


@pytest.fixture
def screen() -> io.StringIO:
    return io.StringIO()


def _answers(monkeypatch: pytest.MonkeyPatch, *replies: str) -> None:
    queue: Iterator[str] = iter(replies)
    monkeypatch.setattr(builtins, "input", lambda *_: next(queue))


def test_card_shows_what_the_decision_is_about(
    console: Console, screen: io.StringIO
) -> None:
    render_card(console, _VIEW, mandatory=False)
    output = screen.getvalue()
    assert "shell_run" in output
    assert "rm -rf build" in output
    assert "build/a" in output
    assert "build/b" in output


def test_mandatory_is_marked(console: Console, screen: io.StringIO) -> None:
    """Mandatory Ask 只能一次性批准; 不标出来会被当成界面漏了一个选项."""
    render_card(console, _VIEW, mandatory=True)
    assert "必须逐次确认" in screen.getvalue()


def test_scopes_map_to_the_brokers_vocabulary(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answers(monkeypatch, "1")
    assert ask_decision(console, _VIEW) == "once"
    _answers(monkeypatch, "2")
    assert ask_decision(console, _VIEW) == "workspace"
    _answers(monkeypatch, "3")
    assert ask_decision(console, _VIEW) == "deny"


def test_scopes_the_view_forbids_are_not_offered(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只列 allowed_scopes 里有的; 多列一个等于让用户选一个 broker 会拒收的决定."""
    once_only = {
        **_VIEW,
        "allowed_scopes": ["once"],
        "learn_blocked_reason": "命令不封闭",
    }
    _answers(monkeypatch, "2")
    assert ask_decision(console, once_only) == "deny"


def test_blocked_reason_is_shown_rather_than_guessed(
    console: Console, screen: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    once_only = {
        **_VIEW,
        "allowed_scopes": ["once"],
        "learn_blocked_reason": "命令不封闭",
    }
    _answers(monkeypatch, "1")
    ask_decision(console, once_only)
    assert "命令不封闭" in screen.getvalue()


def test_interrupt_is_not_a_denial(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C 是"停止这一轮", 不是"我拒绝".

    判成拒绝, 模型会收到一条用户从来没说过的拒绝, 并据此往下走 —— 而用户以为自己只是
    把这一轮停了.
    """

    def interrupt(*_: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", interrupt)
    assert ask_decision(console, _VIEW) is None


def test_bad_input_reasks(console: Console, monkeypatch: pytest.MonkeyPatch) -> None:
    _answers(monkeypatch, "9", "x", "3")
    assert ask_decision(console, _VIEW) == "deny"


def test_interactive_default_focus_is_deny(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forgecli.interfaces.tui import approval_prompt

    def pick_default(
        _console: object, options: object, *, default_index: int
    ) -> object:
        return options[default_index]  # type: ignore[index]

    monkeypatch.setattr(approval_prompt, "select_one", pick_default)
    assert ask_decision(console, _VIEW) == "deny"


def test_interactive_details_can_expand_before_deciding(
    console: Console, screen: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forgecli.interfaces.tui import approval_prompt

    source = "\n".join(f"line {index}" for index in range(12))
    view = {
        **_VIEW,
        "script_snapshots": [
            {"language": "bash", "origin": "inline", "path": "", "source": source}
        ],
    }
    choices = iter(["__details__", "deny"])

    def pick(_console: object, options: object, **_kw: object) -> object:
        key = next(choices)
        return next(item for item in options if item.key == key)  # type: ignore[attr-defined]

    monkeypatch.setattr(approval_prompt, "select_one", pick)
    assert ask_decision(console, view) == "deny"
    assert "完整审批详情" in screen.getvalue()
    assert "line 11" in screen.getvalue()


def test_card_reads_the_same_payload_the_web_gets(
    console: Console, screen: io.StringIO
) -> None:
    """卡片读的键必须是 `ApprovalView.to_payload()` 真的发出来的那些.

    上面那些用例喂的是手写字典, 手写字典永远和被测代码对得上. 真正会漂的是这一头:
    视图改了一个键名, 终端会安静地少画一样东西 —— 而少画的可能正是脚本正文.
    """
    from forgecli.domain.security.approval import ApprovalView
    from forgecli.domain.security.vocabulary import ApprovalScope
    from support.fakes import tool_plan

    view = ApprovalView(
        plan=tool_plan(raw_command="rm -rf build"),
        action_summary="执行 Shell 命令",
        mode="workspace_write/always",
        workspace_roots=("/repo",),
        allowed_scopes=(ApprovalScope.ONCE, ApprovalScope.WORKSPACE),
    )
    payload = view.to_payload()
    render_card(console, payload, mandatory=False)
    output = screen.getvalue()
    assert "shell_run" in output
    assert "rm -rf build" in output
    assert "/repo" in output
