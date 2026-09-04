"""菜单的可见窗口.

`rich.Live` 不滚动: 画得比屏幕高, 顶上的选项就被顶出可视区, 而那几行**不会**进滚动
历史 —— Live 每帧都在原地重画, 用户往上翻只能翻到菜单出现之前的东西. 所以"放不下"
必须由菜单自己处理, 而不是指望终端.
"""

from __future__ import annotations

import io

from rich.console import Console

from forgecli.interfaces.tui.select import (
    Option,
    _render,
    _visible_rows,
    _window,
)

_MANY = tuple(Option(f"k{index}", f"选项 {index}") for index in range(20))


def _console(height: int) -> Console:
    return Console(file=io.StringIO(), width=80, height=height, no_color=True)


def test_short_list_shows_everything() -> None:
    assert _visible_rows(_console(40), 9) == 9


def test_long_list_leaves_room_for_the_markers() -> None:
    """滚动之后还要两行放"上面/下面还有几项", 它们也占位置."""
    visible = _visible_rows(_console(24), 40)
    assert visible == 24 - 4 - 2


def test_a_tiny_terminal_still_gets_usable_rows() -> None:
    """再挤也要给几行: 一个只画得下提示行的菜单没法用."""
    assert _visible_rows(_console(5), 40) >= 3


def test_window_keeps_the_cursor_centred() -> None:
    assert _window(total=20, index=10, visible=5) == 8


def test_window_sticks_to_the_ends() -> None:
    """两端贴边, 不留空行 —— 也不越界."""
    assert _window(total=20, index=0, visible=5) == 0
    assert _window(total=20, index=19, visible=5) == 15


def test_window_is_zero_when_everything_fits() -> None:
    assert _window(total=3, index=2, visible=9) == 0


def test_render_only_draws_the_window() -> None:
    body = _render(_MANY, index=10, visible=5).plain
    assert "选项 10" in body
    assert "选项 0" not in body
    assert "选项 19" not in body


def test_render_says_how_much_is_hidden() -> None:
    """不说还有多少, 用户不知道该继续按还是已经到头了."""
    body = _render(_MANY, index=10, visible=5).plain
    assert "上面还有 8 项" in body
    assert "下面还有 7 项" in body


def test_render_drops_the_markers_when_nothing_is_hidden() -> None:
    body = _render(_MANY[:3], index=0, visible=5).plain
    assert "还有" not in body
    assert "选项 2" in body


def test_tabs_only_show_up_with_more_than_one_group() -> None:
    """一个只有一项的标签栏是噪音."""
    from forgecli.interfaces.tui.select import Group, _render_tabs

    groups = (Group("界面", (Option("a", "A"),)), Group("日志", (Option("b", "B"),)))
    assert "界面" in _render_tabs(groups, 0).plain
    assert "日志" in _render_tabs(groups, 0).plain


def test_tabs_leave_room_in_the_viewport() -> None:
    """分类头也占一行; 不减掉它, 有分类的菜单就比没分类的高一行, 正好溢出."""
    from forgecli.interfaces.tui.select import _visible_rows

    assert (
        _visible_rows(_console(24), 40, tabs=True)
        == _visible_rows(_console(24), 40, tabs=False) - 1
    )


def test_hint_line_only_advertises_keys_that_work() -> None:
    """只有一个分类时写着"←→ 分类"是在教一个按下去没反应的键."""
    flat = _render(_MANY[:3], index=0, visible=3).plain
    grouped = _render(_MANY[:3], index=0, visible=3, tabs=True).plain
    assert "←→ 分类" not in flat
    assert "←→ 分类" in grouped
