"""终端提示卡片的判据 (ADR-0043 决策 11).

审批分支画的是与 Web 同一份 `ApprovalView.to_payload()`. 少画一样东西不会有任何东西
报错, 只会让用户在终端上批准一次他没读全的调用.
"""

from __future__ import annotations

import builtins
import io
from collections.abc import Iterator

import pytest
from rich.console import Console

from forgecli.interfaces.tui.prompt_card import ask_decision, render_card

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
    "mandatory": False,
}

_SCOPES = [
    {"value": "once", "label": "允许这一次", "detail": ""},
    {"value": "workspace", "label": "本工作区始终允许", "detail": ""},
    {"value": "deny", "label": "拒绝", "detail": ""},
]


def _approval(**overrides: object) -> dict[str, object]:
    detail = {**_VIEW, **(overrides.pop("detail", {}) or {})}  # type: ignore[dict-item]
    prompt: dict[str, object] = {
        "prompt_id": "a1",
        "kind": "approval",
        "title": "需要你确认: shell_run",
        "body": "rm -rf build",
        "choices": list(_SCOPES),
        "free_text": False,
        "detail": detail,
    }
    prompt.update(overrides)
    return prompt


def _question(**overrides: object) -> dict[str, object]:
    prompt: dict[str, object] = {
        "prompt_id": "q1",
        "kind": "question",
        "title": "软删除用哪一种?",
        "body": "",
        "choices": [
            {"value": "deleted_at", "label": "deleted_at 时间戳", "detail": ""},
            {"value": "is_deleted", "label": "is_deleted 布尔", "detail": ""},
        ],
        "free_text": True,
        "detail": {},
    }
    prompt.update(overrides)
    return prompt


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
    render_card(console, _approval())
    output = screen.getvalue()
    assert "shell_run" in output
    assert "rm -rf build" in output
    assert "build/a" in output
    assert "build/b" in output


def test_mandatory_is_marked(console: Console, screen: io.StringIO) -> None:
    """Mandatory Ask 只能一次性批准; 不标出来会被当成界面漏了一个选项."""
    render_card(console, _approval(detail={"mandatory": True}))
    assert "必须逐次确认" in screen.getvalue()


def test_scopes_map_to_the_channel_vocabulary(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answers(monkeypatch, "1")
    assert ask_decision(console, _approval()) == ("once", "")
    _answers(monkeypatch, "2")
    assert ask_decision(console, _approval()) == ("workspace", "")
    _answers(monkeypatch, "3")
    assert ask_decision(console, _approval()) == ("deny", "")


def test_scopes_the_prompt_forbids_are_not_offered(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只列提示自己摆出来的选项; 多列一个等于让用户选一个通道会拒收的决定."""
    once_only = _approval(
        choices=[_SCOPES[0], _SCOPES[2]],
        detail={"allowed_scopes": ["once"], "learn_blocked_reason": "命令不封闭"},
    )
    _answers(monkeypatch, "2")
    assert ask_decision(console, once_only) == ("deny", "")


def test_blocked_reason_is_shown_rather_than_guessed(
    console: Console, screen: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    once_only = _approval(
        choices=[_SCOPES[0], _SCOPES[2]],
        detail={"allowed_scopes": ["once"], "learn_blocked_reason": "命令不封闭"},
    )
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
    assert ask_decision(console, _approval()) is None


def test_bad_input_reasks(console: Console, monkeypatch: pytest.MonkeyPatch) -> None:
    _answers(monkeypatch, "9", "x", "3")
    assert ask_decision(console, _approval()) == ("deny", "")


def test_interactive_default_focus_is_deny(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forgecli.interfaces.tui import prompt_card

    def pick_default(
        _console: object, options: object, *, default_index: int
    ) -> object:
        return options[default_index]  # type: ignore[index]

    monkeypatch.setattr(prompt_card, "select_one", pick_default)
    assert ask_decision(console, _approval()) == ("deny", "")


def test_interactive_details_can_expand_before_deciding(
    console: Console, screen: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forgecli.interfaces.tui import prompt_card

    source = "\n".join(f"line {index}" for index in range(12))
    prompt = _approval(
        detail={
            "script_snapshots": [
                {"language": "bash", "origin": "inline", "path": "", "source": source}
            ]
        }
    )
    choices = iter(["__details__", "deny"])

    def pick(_console: object, options: object, **_kw: object) -> object:
        key = next(choices)
        return next(item for item in options if item.key == key)  # type: ignore[attr-defined]

    monkeypatch.setattr(prompt_card, "select_one", pick)
    assert ask_decision(console, prompt) == ("deny", "")
    assert "完整审批详情" in screen.getvalue()
    assert "line 11" in screen.getvalue()


def test_question_shows_the_question_and_offers_free_text(
    console: Console, screen: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提问分支: 选项是建议, 自由文本永远可答 (ADR-0043 决策 7)."""
    render_card(console, _question())
    assert "软删除用哪一种?" in screen.getvalue()
    _answers(monkeypatch, "都不对, 用 status 字段")
    assert ask_decision(console, _question()) == ("", "都不对, 用 status 字段")


def test_question_option_comes_back_as_a_choice(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answers(monkeypatch, "1")
    assert ask_decision(console, _question()) == ("deleted_at", "")


def test_question_without_options_reads_a_line(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只收自由文本时不摆菜单: 一个只有一项的菜单是在让人多按一次回车."""
    _answers(monkeypatch, "用 Postgres")
    assert ask_decision(console, _question(choices=[])) == ("", "用 Postgres")


def test_question_default_focus_is_the_first_option(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提问的任何一项都不产生授权, 所以焦点不必躲到最后一项去."""
    from forgecli.interfaces.tui import prompt_card

    def pick_default(
        _console: object, options: object, *, default_index: int
    ) -> object:
        return options[default_index]  # type: ignore[index]

    monkeypatch.setattr(prompt_card, "select_one", pick_default)
    assert ask_decision(console, _question()) == ("deleted_at", "")


def test_card_reads_the_same_payload_the_channel_sends(
    console: Console, screen: io.StringIO
) -> None:
    """卡片读的键必须是 `_prompt_of()` 真的发出来的那些.

    上面那些用例喂的是手写字典, 手写字典永远和被测代码对得上. 真正会漂的是这一头:
    视图改了一个键名, 终端会安静地少画一样东西 —— 而少画的可能正是脚本正文.
    """
    from forgecli.application.security.approval_service import _prompt_of
    from forgecli.domain.intents import SessionMode
    from forgecli.domain.security.approval import (
        ApprovalBinding,
        ApprovalRequest,
        ApprovalView,
    )
    from forgecli.domain.security.vocabulary import ApprovalScope
    from support.fakes import tool_plan

    plan = tool_plan(raw_command="rm -rf build")
    view = ApprovalView(
        plan=plan,
        action_summary="执行 Shell 命令",
        mode="workspace_write/always",
        workspace_roots=("/repo",),
        allowed_scopes=(ApprovalScope.ONCE, ApprovalScope.WORKSPACE),
    )
    request = ApprovalRequest(
        approval_id="a1",
        binding=ApprovalBinding(
            plan_hash=plan.plan_hash,
            catalog_snapshot_hash="c",
            execution_profile_hash="e",
            policy_version="1",
            mode=SessionMode.from_value("workspace_write/always"),
            view_hash=view.view_hash,
        ),
        view=view,
    )
    render_card(console, _prompt_of(request).to_payload())
    output = screen.getvalue()
    assert "shell_run" in output
    assert "rm -rf build" in output
    assert "/repo" in output
