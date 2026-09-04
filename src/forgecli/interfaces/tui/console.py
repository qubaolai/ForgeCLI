"""终端外观: 一处定义颜色, 字形与几个反复出现的排版块.

为什么单独一个模块: 同一种东西在四个命令里各画一遍, 迟早会出现"模式"在 `/status` 里
是青色而在 `/mode` 里是黄色这种事 —— 两边都不会报错, 只会让人以为它们是两个东西.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

# 时间线字形. 与 Web 的轨道圆点同义 (ADR-0025 决策 25): 只区分状态, 不区分类型 ——
# 过程区是扫读区, 一行一个图标只会分散注意力.
DOT = "⏺"
SUB = "⎿"
THINK = "✻"
ASK = "⏵"

# 与 Web 控制面共享同一组语义色：不是要求终端逐像素复刻网页，而是让“强调 / 警告 /
# 危险 / 成功”在两个入口表达同一件事。Rich 会在低色终端自动降级。
STYLE_DIM = "#9198a7"
STYLE_ACCENT = "#62d6ad"
STYLE_WARN = "#f4bd61"
STYLE_ERROR = "#ef7a81"
STYLE_OK = "#62d6ad"


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


def display_path(path: str) -> str:
    """把家目录折成 ``~``.

    只用于展示, 绝不用来解析: 折回去要再猜一次 ``~`` 指哪里, 而路径是安全判断的输入.

    值得一个函数是因为它省下的是**每一行**的宽度. 一条
    ``/Users/someone/Documents/git_repostory/ForgeCLI`` 里前 25 列对读的人零信息, 却
    足够把工作区那一行挤到换行 —— 而它出现在启动屏, /status, /dirs, /projects 和每一张
    审批卡片上.
    """
    if not path:
        return path
    try:
        home = str(Path.home())
    except (OSError, RuntimeError):  # pragma: no cover - 拿不到家目录就原样给
        return path
    if path == home:
        return "~"
    prefix = home.rstrip("/") + "/"
    return "~/" + path[len(prefix) :] if path.startswith(prefix) else path


def shorten_path(path: str, limit: int = 56) -> str:
    """路径太长时留尾巴, 不留头.

    与 `truncate` 相反: 一串同前缀的路径, 砍掉尾巴之后每一条看起来都一样, 而尾巴正是
    它们唯一的区别. 完整路径仍然在上面那张表里逐字列着 —— 这里短的只是菜单项的标签.

    先折家目录再判长度: 折完多半就不用截了, 而带 ``…`` 的那一版没法复制粘贴.
    """
    path = display_path(path)
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1) :]


def grouped_listing(
    groups: Iterable[tuple[str, Iterable[tuple[str, str]]]],
) -> Table:
    """分组清单: 每组一个小标题, **所有组共用一套列宽**.

    一组一张表是天然的写法, 也是错的: 每张表各自算列宽, 于是九个分组的说明列在九个
    不同的位置起头, 整块扫下来是锯齿状的; 而"命令 / 说明"那行表头会跟着重复九遍,
    占掉的行数比它解释的东西还多.
    """
    table = Table.grid(padding=(0, 3))
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")
    for index, (title, rows) in enumerate(groups):
        if index:
            table.add_row("", "")
        table.add_row(Text(title, style=f"bold {STYLE_ACCENT}"), "")
        for name, summary in rows:
            table.add_row(Text(f"  {name}"), Text(summary, style=STYLE_DIM))
    return table


def truncate(text: str, limit: int) -> str:
    """一行摘要的截断. 终端里一条命令可能有几千字符, 全打出来会把时间线冲掉."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"
