"""编号菜单与几个小问句.

Web 上一个下拉框在终端里就是"列出来 + 选一个". 这件事在模式, 模型, 恢复点, 计划,
供应商每一处都要做一遍, 所以只写一份 —— 各写各的话, 有的地方 0 是取消, 有的地方回车
是取消, 用户每进一个菜单都要重新学一次.

选择走 `select_one`: ↑↓ 移动, Enter 确认, 数字键直选. 只在拿不到终端时 (管道, CI,
用例) 才退回"输一个编号" —— 那条路仍然要留着, 但它不是主路径.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from forgecli.interfaces.tui.console import STYLE_ACCENT, STYLE_DIM, error
from forgecli.interfaces.tui.select import Option, SelectUnavailable, select_one

__all__ = ["Option", "ask_text", "choose", "confirm"]

# 取消菜单的输入. 直接回车也取消: 那是"我只是想看看"最自然的退出方式.
_CANCEL = {"q", "quit", "cancel", "exit"}
_YES = {"y", "yes"}
_NO = {"n", "no"}


def choose(
    console: Console,
    title: str,
    options: Sequence[Option],
    *,
    current: str = "",
) -> Option | None:
    """列出选项并读一个选择. 返回 None 表示用户取消.

    ``current`` 命中的那一项作为初始高亮: 没有它, 用户要在菜单和 `/status` 之间来回跑
    才知道现在是哪一档.
    """
    if not options:
        error(console, "没有可选项")
        return None
    console.print(Text(title, style=f"bold {STYLE_ACCENT}"))
    default = next(
        (index for index, item in enumerate(options) if item.key == current), 0
    )
    try:
        return select_one(console, options, default_index=default)
    except SelectUnavailable:
        return _numbered(console, options)


def _numbered(console: Console, options: Sequence[Option]) -> Option | None:
    """没有终端时的回退: 打印一遍再读一个编号."""
    for index, option in enumerate(options, start=1):
        line = Text(f"  {index}. ", style=STYLE_ACCENT)
        line.append(option.label)
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
