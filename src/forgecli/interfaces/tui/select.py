"""单层单选: ↑↓ 移动, Enter 选中, 数字键直选.

菜单要能用方向键走 —— 一个只能"输编号"的界面在选项超过五个之后就要求用户先数一遍.
这里不做子菜单, 不做搜索, 也不改任何状态: 它只回答"从 N 个里挑一个".

三条性质:

- 取消 (Esc / Ctrl-C) 不返回任何选项, 由调用方决定怎么处理.
- 数字键直接确认, 不只是移动光标: 按下 "3" 的人已经决定了.
- 不是真终端时抛 `SelectUnavailable`, 调用方回退到读一行. 这里不自己做回退, 免得把
  "没有终端"这件事藏在一个看起来正常的返回值里.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from rich.console import Console
from rich.live import Live
from rich.text import Text

from forgecli.interfaces.tui.tty import (
    CONFIRM_KEYS,
    KeyReader,
    Keys,
    is_text,
    stdin_is_tty,
)

__all__ = ["Option", "SelectUnavailable", "select_one"]

# 菜单之外要留给终端的行数: 提示行, 上一条输出, 以及不贴着屏幕底边.
#
# 必须留: `rich.Live` 不滚动, 画得比屏幕高就是把顶上的选项顶出可视区, 而那几行**不会**
# 进滚动历史 —— Live 每帧都在原地重画, 用户往上翻只能翻到菜单出现之前的东西.
_RESERVED_ROWS = 4
# 开始滚动之后还要两行放"上面/下面还有几项", 它们也占位置.
_SCROLL_MARKER_ROWS = 2
# 再挤也至少给三行选项: 少于这个数, 菜单本身就没法用了.
_MIN_VISIBLE = 3

_CURSOR = "#94e2d5"
_NORMAL = "#9399b2"
_DETAIL = "#6c7086"
_HINT = "bright_black"


class SelectUnavailable(RuntimeError):
    """当前环境没有可交互的终端."""


@dataclass(frozen=True)
class Option:
    """一个可选项. ``key`` 是调用方用来识别选择结果的稳定标识, 不展示给用户."""

    key: str
    label: str
    hint: str = ""


def _require_readable_stdin() -> None:
    """stdin 必须有文件描述符, 否则这里读不了键.

    stdin 未必有 fileno —— 被替换成内存流时 (pytest 捕获, 某些嵌入环境) 会抛
    UnsupportedOperation. 那种情况下 raw mode 本来就做不了, 归一成 SelectUnavailable
    让调用方走回退路径.

    必须显式拦: `create_input()` 对这种 stdin 会安静地返回一个 DummyInput, 于是"没有
    终端"会表现成"用户按了取消" —— 一个看起来完全正常的返回值.
    """
    try:
        sys.stdin.fileno()
    except (OSError, ValueError, io.UnsupportedOperation) as exc:
        raise SelectUnavailable("stdin 没有文件描述符, 无法进入 raw mode") from exc


def select_one(
    console: Console,
    options: Sequence[Option],
    *,
    default_index: int = 0,
) -> Option | None:
    """让用户选一项. 返回 None 表示取消 (Esc / Ctrl-C).

    ``default_index`` 是初始高亮项 —— 直接回车就选它.
    """
    if not options:
        raise ValueError("select_one 至少需要一个选项")
    if not stdin_is_tty():
        raise SelectUnavailable("当前不在终端中, 无法交互式选择")

    _require_readable_stdin()
    index = max(0, min(default_index, len(options) - 1))
    live = Live(
        _render(options, index, _visible_rows(console, len(options))),
        console=console,
        transient=True,
        auto_refresh=False,
    )
    with live, KeyReader() as keys:
        while True:
            press = keys.read()
            if press.key is Keys.Up:
                index = (index - 1) % len(options)
            elif press.key is Keys.Down:
                index = (index + 1) % len(options)
            elif press.key in CONFIRM_KEYS:
                return options[index]
            elif press.key in (Keys.Escape, Keys.ControlC):
                return None
            elif is_text(press) and press.data.isdigit():
                picked = int(press.data) - 1
                if 0 <= picked < len(options):
                    return options[picked]
                continue
            else:
                continue
            # 每帧现算可见行数: 用户拉窗口是常事, 而拉窄之后按原来的高度画就又溢出了.
            live.update(
                _render(options, index, _visible_rows(console, len(options))),
                refresh=True,
            )


def _visible_rows(console: Console, total: int) -> int:
    """这一屏能放下几个选项."""
    room = console.size.height - _RESERVED_ROWS
    if total <= room:
        return total
    return max(_MIN_VISIBLE, room - _SCROLL_MARKER_ROWS)


def _window(total: int, index: int, visible: int) -> int:
    """可见窗口从第几项开始. 光标尽量居中, 到两端就贴边."""
    if total <= visible:
        return 0
    return min(max(0, index - visible // 2), total - visible)


def _render(options: Sequence[Option], index: int, visible: int) -> Text:
    start = _window(len(options), index, visible)
    body = Text()
    if start > 0:
        body.append(f"  ↑ 上面还有 {start} 项\n", style=_HINT)
    for position in range(start, min(start + visible, len(options))):
        option = options[position]
        current = position == index
        body.append("❯ " if current else "  ", style=_CURSOR)
        body.append(
            f"{position + 1}. {option.label}",
            style=_CURSOR if current else _NORMAL,
        )
        if option.hint:
            body.append(f"  {option.hint}", style=_DETAIL)
        body.append("\n")
    rest = len(options) - (start + visible)
    if rest > 0:
        body.append(f"  ↓ 下面还有 {rest} 项\n", style=_HINT)
    body.append("↑↓ 选择 · Enter 确认 · 数字键直选 · Esc 取消", style=_HINT)
    return body
