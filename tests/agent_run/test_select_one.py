"""单层单选控件 (审批界面用它替代"敲数字")."""

from __future__ import annotations

import io
from collections.abc import Iterator
from types import TracebackType

import pytest
from rich.console import Console

from forgecli.interfaces.cli.tty import select as select_module
from forgecli.interfaces.cli.tty.select import (
    SelectOption,
    SelectUnavailable,
    select_one,
)
from forgecli.interfaces.cli.tty.tty import KeyPress, Keys

OPTIONS = (
    SelectOption("once", "once", "仅本次"),
    SelectOption("always", "always", "本工作区内"),
    SelectOption("deny", "deny", "拒绝"),
)

ENTER = KeyPress(Keys.ControlM)


def _console() -> Console:
    return Console(file=io.StringIO(), width=80, record=True, no_color=True)


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """把 KeyReader 换成一串脚本化按键.

    替掉整个 reader 而不是替它读键的那一步: raw mode 与真终端是它自己的事, 这些用例问的
    是"给定这串按键, 选中了谁".
    """

    def install(presses: list[KeyPress]) -> None:
        stream: Iterator[KeyPress] = iter(presses)

        class _ScriptedReader:
            def __enter__(self) -> _ScriptedReader:
                return self

            def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: TracebackType | None,
            ) -> None:
                return None

            def read(self) -> KeyPress:
                return next(stream)

        monkeypatch.setattr(select_module, "stdin_is_tty", lambda: True)
        monkeypatch.setattr(select_module, "_require_readable_stdin", lambda: None)
        monkeypatch.setattr(select_module, "KeyReader", _ScriptedReader)

    return install


def test_enter_takes_the_highlighted_default(keys) -> None:  # type: ignore[no-untyped-def]
    keys([ENTER])
    assert select_one(_console(), OPTIONS).key == "once"  # type: ignore[union-attr]


def test_down_then_enter_moves_the_selection(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress(Keys.Down), ENTER])
    assert select_one(_console(), OPTIONS).key == "always"  # type: ignore[union-attr]


def test_up_wraps_to_the_last_option(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress(Keys.Up), ENTER])
    assert select_one(_console(), OPTIONS).key == "deny"  # type: ignore[union-attr]


def test_newline_also_confirms(keys) -> None:  # type: ignore[no-untyped-def]
    """粘贴进来的换行是 ControlJ 而不是 ControlM; 两个都该算确认."""
    keys([KeyPress(Keys.ControlJ)])
    assert select_one(_console(), OPTIONS).key == "once"  # type: ignore[union-attr]


def test_a_digit_confirms_directly(keys) -> None:  # type: ignore[no-untyped-def]
    """按下 "3" 的人已经决定了, 不该只是移动光标."""
    keys([KeyPress("3")])
    assert select_one(_console(), OPTIONS).key == "deny"  # type: ignore[union-attr]


def test_an_out_of_range_digit_is_ignored(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress("9"), ENTER])
    assert select_one(_console(), OPTIONS).key == "once"  # type: ignore[union-attr]


@pytest.mark.parametrize("key", [Keys.Escape, Keys.ControlC])
def test_cancelling_returns_nothing(keys, key: Keys) -> None:  # type: ignore[no-untyped-def]
    """取消不返回任何选项 —— 由调用方决定它意味着什么 (审批那边是拒绝)."""
    keys([KeyPress(key)])
    assert select_one(_console(), OPTIONS) is None


def test_default_index_can_be_moved(keys) -> None:  # type: ignore[no-untyped-def]
    keys([ENTER])
    chosen = select_one(_console(), OPTIONS, default_index=2)
    assert chosen.key == "deny"  # type: ignore[union-attr]


def test_unknown_keys_do_not_choose_anything(keys) -> None:  # type: ignore[no-untyped-def]
    keys([KeyPress(Keys.ControlI), KeyPress(Keys.Left), ENTER])
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
