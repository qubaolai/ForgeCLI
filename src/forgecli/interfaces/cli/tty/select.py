"""单层单选: ↑↓ 移动, Enter 选中.

与 MenuPresenter 的区别是它**不导航**: 没有子菜单, 没有搜索, 没有行内编辑, 也不会改任何
状态. 审批只需要"从 N 个里挑一个", 用整套菜单机制反而要把不需要的按键语义一并带进来.

三条给审批用的性质:

- 取消 (Esc / Ctrl-C) 不返回任何选项, 由调用方决定怎么处理. 对审批来说那就是拒绝.
- 数字键作为快捷键: 手记得住 "3 是拒绝" 的人不必先移动光标.
- 不是真终端时抛 SelectUnavailable, 调用方回退到读一行. 这里不自己做回退, 免得把
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

from forgecli.interfaces.cli.tty.tty import (
    CONFIRM_KEYS,
    KeyReader,
    Keys,
    is_text,
    stdin_is_tty,
)

__all__ = ["SelectOption", "SelectUnavailable", "select_one"]

_CURSOR = "#94e2d5"
_NORMAL = "#9399b2"
_DETAIL = "#6c7086"
_HINT = "bright_black"


class SelectUnavailable(RuntimeError):
    """当前环境没有可交互的终端."""


def _require_readable_stdin() -> None:
    """stdin 必须有文件描述符, 否则这里读不了键.

    stdin 未必有 fileno —— 被替换成内存流时 (pytest 捕获, 某些嵌入环境) 会抛
    UnsupportedOperation. 那种情况下 raw mode 本来就做不了, 归一成 SelectUnavailable
    让调用方走回退路径.

    必须显式拦: `create_input()` 对这种 stdin 会安静地返回一个 DummyInput, 于是"没有
    终端"会表现成"用户按了取消"—— 一个看起来完全正常的返回值.
    """
    try:
        sys.stdin.fileno()
    except (OSError, ValueError, io.UnsupportedOperation) as exc:
        raise SelectUnavailable("stdin 没有文件描述符, 无法进入 raw mode") from exc


@dataclass(frozen=True)
class SelectOption:
    """一个可选项. key 是调用方用来识别选择结果的稳定标识, 不展示给用户."""

    key: str
    label: str
    detail: str = ""


def select_one(
    console: Console,
    options: Sequence[SelectOption],
    *,
    default_index: int = 0,
) -> SelectOption | None:
    """让用户选一项. 返回 None 表示取消 (Esc / Ctrl-C).

    default_index 是初始高亮项 —— 直接回车就选它.
    """
    if not options:
        raise ValueError("select_one 至少需要一个选项")
    if not stdin_is_tty():
        raise SelectUnavailable("当前不在终端中, 无法交互式选择")

    _require_readable_stdin()
    index = max(0, min(default_index, len(options) - 1))
    live = Live(
        _render(options, index), console=console, transient=True, auto_refresh=False
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
                    # 数字键直接确认, 不只是移动光标: 按下 "3" 的人已经决定了.
                    return options[picked]
                continue
            else:
                continue
            live.update(_render(options, index), refresh=True)


def _render(options: Sequence[SelectOption], index: int) -> Text:
    body = Text()
    for position, option in enumerate(options):
        current = position == index
        body.append("❯ " if current else "  ", style=_CURSOR)
        body.append(
            f"{position + 1}. {option.label}",
            style=_CURSOR if current else _NORMAL,
        )
        if option.detail:
            body.append(f"  {option.detail}", style=_DETAIL)
        body.append("\n")
    body.append("↑↓ 选择 · Enter 确认 · 数字键直选 · Esc 取消", style=_HINT)
    return body
