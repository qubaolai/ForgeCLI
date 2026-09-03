"""终端外观: 一处定义颜色, 字形与几个反复出现的排版块.

为什么单独一个模块: 同一种东西在四个命令里各画一遍, 迟早会出现"模式"在 `/status` 里
是青色而在 `/mode` 里是黄色这种事 —— 两边都不会报错, 只会让人以为它们是两个东西.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

# 时间线字形. 与 Web 的轨道圆点同义 (ADR-0025 决策 25): 只区分状态, 不区分类型 ——
# 过程区是扫读区, 一行一个图标只会分散注意力.
DOT = "⏺"
SUB = "⎿"
THINK = "✻"
ASK = "⏵"

STYLE_DIM = "grey50"
STYLE_ACCENT = "cyan"
STYLE_WARN = "yellow"
STYLE_ERROR = "red"
STYLE_OK = "green"


def make_console() -> Console:
    """终端输出口.

    ``soft_wrap`` 不在这里定死: 流式增量必须软换行 (Rich 无法在两次 print 之间接着
    上一行排版), 而表格与面板要用 Rich 自己的宽度计算.
    """
    return Console(highlight=False, emoji=False)


def rule(console: Console, title: str) -> None:
    console.print()
    console.rule(Text(title, style=STYLE_ACCENT), style=STYLE_DIM)


def hint(console: Console, text: str) -> None:
    console.print(Text(text, style=STYLE_DIM))


def ok(console: Console, text: str) -> None:
    console.print(Text(f"✓ {text}", style=STYLE_OK))


def warn(console: Console, text: str) -> None:
    console.print(Text(f"! {text}", style=STYLE_WARN))


def error(console: Console, text: str) -> None:
    console.print(Text(f"✗ {text}", style=STYLE_ERROR))


def kv_table(rows: Iterable[tuple[str, str]], *, key_style: str = STYLE_DIM) -> Table:
    """两列的"名字: 值"块. 无边框, 因为它出现在正文流里而不是一张独立的表."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style=key_style, justify="right", no_wrap=True)
    table.add_column(overflow="fold")
    for name, value in rows:
        table.add_row(name, value)
    return table


def listing(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> Table:
    """带表头的清单. 列表类命令 (会话, 模型, 恢复点) 共用它, 免得各排各的."""
    table = Table(
        show_edge=False,
        box=None,
        pad_edge=False,
        header_style=f"bold {STYLE_DIM}",
        padding=(0, 2),
    )
    for name in headers:
        # 每一列都可折行. 把第一列钉成不折的话, 一条很长的路径会把它右边的列全部挤没
        # —— 而"这个目录是读还是写"正是那一行要回答的问题.
        table.add_column(name, overflow="fold")
    for row in rows:
        table.add_row(*row)
    return table


def shorten_path(path: str, limit: int = 56) -> str:
    """路径太长时留尾巴, 不留头.

    与 `truncate` 相反: 一串同前缀的路径, 砍掉尾巴之后每一条看起来都一样, 而尾巴正是
    它们唯一的区别. 完整路径仍然在上面那张表里逐字列着 —— 这里短的只是菜单项的标签.
    """
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1) :]


def truncate(text: str, limit: int) -> str:
    """一行摘要的截断. 终端里一条命令可能有几千字符, 全打出来会把时间线冲掉."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"
