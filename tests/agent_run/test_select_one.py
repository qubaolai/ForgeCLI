"""单层单选控件 (审批界面用它替代"敲数字")."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from rich.console import Console

from forgecli.interfaces.cli.tty import select as select_module
from forgecli.interfaces.cli.tty.keys import Key, KeyPress
from forgecli.interfaces.cli.tty.select import (
    SelectOption,
    SelectUnavailable,
    select_one,
)

OPTIONS = (
    SelectOption("once", "once", "仅本次"),
    SelectOption("always", "always", "本工作区内"),
    SelectOption("deny", "deny", "拒绝"),
)


def _console() -> Console:
    return Console(file=io.StringIO(), width=80, record=True, no_color=True)


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """把 raw_mode 与 read_key 换成脚本化按键."""

    def install(presses: list[KeyPress]) -> None:
        stream: Iterator[KeyPress] = iter(presses)
        monkeypatch.setattr(select_module, "stdin_is_tty", lambda: True)
        monkeypatch.setattr(select_module, "_stdin_fd", lambda: 0)
        monkeypatch.setattr(
            select_module, "raw_mode", lambda fd: __import__("contextlib").nullcontext()
        )
        monkeypatch.setattr(select_module, "read_key", lambda fd: next(stream))

    return install


def test_enter_takes_the_highlighted_default(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress(Key.ENTER)])
    assert select_one(_console(), OPTIONS).key == "once"  # type: ignore[union-attr]


def test_down_then_enter_moves_the_selection(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress(Key.DOWN), KeyPress(Key.ENTER)])
    assert select_one(_console(), OPTIONS).key == "always"  # type: ignore[union-attr]


def test_up_wraps_to_the_last_option(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress(Key.UP), KeyPress(Key.ENTER)])
    assert select_one(_console(), OPTIONS).key == "deny"  # type: ignore[union-attr]


def test_a_digit_confirms_directly(keys) -> None:  # type: ignore[no-untyped-def]
    """按下 "3" 的人已经决定了, 不该只是移动光标."""
    keys([KeyPress(Key.CHAR, "3")])
    assert select_one(_console(), OPTIONS).key == "deny"  # type: ignore[union-attr]


def test_an_out_of_range_digit_is_ignored(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress(Key.CHAR, "9"), KeyPress(Key.ENTER)])
    assert select_one(_console(), OPTIONS).key == "once"  # type: ignore[union-attr]


@pytest.mark.parametrize("key", [Key.ESC, Key.CTRL_C])
def test_cancelling_returns_nothing(keys, key: Key) -> None:  # type: ignore[no-untyped-def]
    """取消不返回任何选项 —— 由调用方决定它意味着什么 (审批那边是拒绝)."""
    keys([KeyPress(key)])
    assert select_one(_console(), OPTIONS) is None


def test_default_index_can_be_moved(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress(Key.ENTER)])
    chosen = select_one(_console(), OPTIONS, default_index=2)
    assert chosen.key == "deny"  # type: ignore[union-attr]


def test_unknown_keys_do_not_choose_anything(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress(Key.TAB), KeyPress(Key.LEFT), KeyPress(Key.ENTER)])
    assert select_one(_console(), OPTIONS).key == "once"  # type: ignore[union-attr]


def test_without_a_terminal_it_refuses_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不自己回退: 把"没有终端"藏在一个看起来正常的返回值里最危险."""
    monkeypatch.setattr(select_module, "stdin_is_tty", lambda: False)
    with pytest.raises(SelectUnavailable):
        select_one(_console(), OPTIONS)


def test_no_options_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="至少需要一个选项"):
        select_one(_console(), ())
