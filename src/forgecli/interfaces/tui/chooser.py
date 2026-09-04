"""编号菜单与几个小问句.

Web 上一个下拉框在终端里就是"列出来 + 选一个". 这件事在模式, 模型, 恢复点, 计划,
供应商每一处都要做一遍, 所以只写一份 —— 各写各的话, 有的地方 0 是取消, 有的地方回车
是取消, 用户每进一个菜单都要重新学一次.

选择走 `select_one`: ↑↓ 移动, ←→ 换分类, Enter 确认, 数字键直选, Esc 返回上一层.
只在拿不到终端时 (管道, CI, 用例) 才退回"输一个编号" —— 那条路仍然要留着, 但它不是
主路径.

**菜单不打标题.** 进菜单之前先打一行"要做什么"是在替用户复述他刚敲的命令: 他按了
`/model`, 屏幕上就该直接出现模型的那几项. 命令是干什么的写在 `/help` 和 `/<命令> -h`
里, 不在每次使用时重复一遍.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from forgecli.interfaces.tui.console import STYLE_ACCENT, STYLE_DIM, error
from forgecli.interfaces.tui.select import (
    Group,
    Option,
    SelectUnavailable,
    select_grouped,
    select_one,
)

__all__ = ["Group", "Option", "ask_text", "choose", "choose_grouped", "confirm"]

# 取消菜单的输入. 直接回车也取消: 那是"我只是想看看"最自然的退出方式.
_CANCEL = {"q", "quit", "cancel", "exit"}
_YES = {"y", "yes"}
_NO = {"n", "no"}


def choose(
    console: Console,
    options: Sequence[Option],
    *,
    current: str = "",
) -> Option | None:
    """列出选项并读一个选择. 返回 None 表示用户取消 (Esc 返回上一层).

    ``current`` 命中的那一项作为初始高亮: 没有它, 用户要在菜单和 `/status` 之间来回跑
    才知道现在是哪一档.
    """
    if not options:
        error(console, "没有可选项")
        return None
    try:
        return select_one(console, options, default_index=_index_of(options, current))
    except SelectUnavailable:
        return _numbered(console, options)


def choose_grouped(
    console: Console,
    groups: Sequence[Group],
    *,
    current: str = "",
) -> Option | None:
    """分类版: ←/→ 换分类. 初始停在含 ``current`` 的那一类上."""
    filled = [group for group in groups if group.options]
    if not filled:
        error(console, "没有可选项")
        return None
    group_index = next(
        (
            index
            for index, group in enumerate(filled)
            if any(item.key == current for item in group.options)
        ),
        0,
    )
    try:
        return select_grouped(
            console,
            filled,
            default_index=_index_of(filled[group_index].options, current),
            group_index=group_index,
        )
    except SelectUnavailable:
        return _numbered(console, [item for group in filled for item in group.options])


def _index_of(options: Sequence[Option], current: str) -> int:
    return next((index for index, item in enumerate(options) if item.key == current), 0)


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
