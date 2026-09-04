"""启动横幅.

只依赖 Rich, 不读配置也不碰会话状态: 它在项目解析之前就要画出来.

与当前会话信息**并排**显示. 竖着排的话, 一个二十四行的终端上光是开场就占掉八行, 而
其中六行是同一个不会变的图形 —— 右边那几行才是每次启动都要看一眼的东西.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from forgecli.interfaces.tui.console import STYLE_DIM, kv_table
from forgecli.shared import __version__

# ANSI Shadow 字体的 "FORGE". 保持为文本常量, 免得启动阶段多读一个资源文件.
_LOGO = r"""
███████╗ ██████╗ ██████╗  ██████╗ ███████╗
██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
█████╗  ██║   ██║██████╔╝██║  ███╗█████╗
██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝
██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
"""

# 自上而下的火焰色: 每一项对应 logo 的一行.
_FLAME = ("bright_yellow", "yellow", "orange1", "dark_orange", "orange_red1", "red3")

_TAGLINE = "在终端里锻造代码的 AI 助手"

# 左右分栏要的宽度: logo 本身 42 列, 右栏还要放得下一条工作区路径.
_MIN_WIDTH_FOR_COLUMNS = 110
# 只画 logo 要的宽度. 42 是它本身的宽度, 少一列都会把最右边那道竖线切掉.
_MIN_WIDTH_FOR_LOGO = 42
# logo 占六行. 终端矮到这个数以下, 开场就吃掉四分之一屏.
_MIN_HEIGHT_FOR_LOGO = 30


def logo() -> Text:
    """火焰色的 FORGE.

    ``strict=True`` 让行数与颜色数量对不上时立刻失败 —— 换 ASCII 字体之后悄悄少一行
    或漏一种颜色, 是那种没人会发现的退化.
    """
    body = Text()
    for line, color in zip(_LOGO.strip("\n").splitlines(), _FLAME, strict=True):
        body.append(line + "\n", style=f"bold {color}")
    body.append(_TAGLINE, style=STYLE_DIM)
    body.append(f"   v{__version__}", style="bold cyan")
    return body


def render(console: Console, rows: Sequence[tuple[str, str]]) -> None:
    """左边 logo, 右边这一次会话的事实."""
    facts = kv_table(rows)
    width, height = console.size.width, console.size.height
    if width < _MIN_WIDTH_FOR_LOGO or height < _MIN_HEIGHT_FOR_LOGO:
        # 挤不下就只留事实: 半个 logo 比没有 logo 难看得多.
        console.print(Text(f"Forge v{__version__}", style="bold cyan"))
        console.print(facts)
        return
    if width < _MIN_WIDTH_FOR_COLUMNS:
        # 分不了栏但画得下 logo 时上下叠着放, 而不是把 logo 整个丢掉 —— 80 到 110 列
        # 是最常见的终端宽度, 按"要么分栏要么没有"处理, 等于大多数人从来见不到它.
        console.print(logo())
        console.print(facts)
        return
    layout = Table.grid(padding=(0, 4))
    layout.add_column(no_wrap=True)
    layout.add_column(overflow="fold")
    layout.add_row(logo(), facts)
    console.print(layout)
