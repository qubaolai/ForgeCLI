"""编号菜单与几个小问句.

Web 上一个下拉框在终端里就是"列出来 + 输编号". 这件事在模式, 模型, 恢复点, 计划,
供应商每一处都要做一遍, 所以只写一份 —— 各写各的话, 有的地方 0 是取消, 有的地方
回车是取消, 用户每进一个菜单都要重新学一次.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rich.console import Console
from rich.text import Text

from forgecli.interfaces.tui.console import (
    STYLE_ACCENT,
    STYLE_DIM,
    error,
)

# 取消菜单的输入. 直接回车也取消: 那是"我只是想看看"最自然的退出方式.
_CANCEL = {"q", "quit", "cancel", "exit"}
_YES = {"y", "yes"}
_NO = {"n", "no"}


@dataclass(frozen=True)
class Option:
    """菜单里的一项. ``key`` 是回给调用方的值, 不是显示给人的字符串."""

    key: str
    label: str
    hint: str = ""


def choose(
    console: Console,
    title: str,
    options: Sequence[Option],
    *,
    current: str = "",
) -> Option | None:
    """列出选项并读一个编号. 返回 None 表示用户取消.

    ``current`` 命中的那一项标一个点: 没有它, 用户要在菜单和 `/status` 之间来回跑才
    知道现在是哪一档.
    """
    if not options:
        error(console, "没有可选项")
        return None
    console.print(Text(title, style=f"bold {STYLE_ACCENT}"))
    for index, option in enumerate(options, start=1):
        marker = "●" if option.key == current else " "
        line = Text(f" {marker} {index}. ", style=STYLE_ACCENT)
        line.append(option.label, style="bold" if option.key == current else "")
        if option.hint:
            line.append(f"  {option.hint}", style=STYLE_DIM)
        console.print(line)
    while True:
        raw = input("选择编号 (回车取消): ").strip()
        if not raw or raw.lower() in _CANCEL:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        error(console, f"输入 1-{len(options)} 之间的编号, 或直接回车取消")


def ask_text(
    console: Console,
    label: str,
    *,
    default: str = "",
    allow_empty: bool = False,
) -> str | None:
    """读一行文本. 返回 None 表示取消 (空输入且没有默认值, 或不允许空)."""
    suffix = f" [{default}]" if default else ""
    raw = input(f"{label}{suffix}: ").strip()
    if not raw and default:
        return default
    if not raw and not allow_empty:
        console.print(Text("已取消", style=STYLE_DIM))
        return None
    return raw


def confirm(console: Console, question: str, *, default: bool = False) -> bool:
    """是非问句. 认不出的输入重问, 不猜 —— 猜错的那一次是替用户按了确认."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{question} {suffix} ").strip().lower()
        if not raw:
            return default
        if raw in _YES:
            return True
        if raw in _NO:
            return False
        console.print(Text("请输入 y 或 n", style=STYLE_DIM))
