"""ForgeCLI 启动横幅渲染。

横幅属于 CLI 展示层，只依赖 Rich，不包含配置读取、会话状态或业务判断。
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from forgecli.shared import __version__

# ANSI Shadow 字体的 "FORGE"。保持为文本常量，避免在启动阶段引入额外资源。
_LOGO = r"""
███████╗ ██████╗ ██████╗  ██████╗ ███████╗
██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
█████╗  ██║   ██║██████╔╝██║  ███╗█████╗  
██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  
██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
"""

# 自上而下的火焰色：每一项对应 logo 的一行。
_FLAME = ["bright_yellow", "yellow", "orange1", "dark_orange", "orange_red1", "red3"]

_TAGLINE = "在终端里锻造代码的 AI 助手"


def render_banner(console: Console) -> None:
    """打印 Forge 启动 banner。

    ``strict=True`` 让 logo 行数与颜色数量不匹配时立刻失败，避免改动 ASCII
    字体后悄悄丢行或漏色。
    """
    logo = Text()
    for text_line, color in zip(_LOGO.strip("\n").splitlines(), _FLAME, strict=True):
        logo.append(text_line + "\n", style=f"bold {color}")

    console.print(logo)
    console.print(
        Text.assemble(
            (_TAGLINE, "dim"),
            ("   ", ""),
            (f"v{__version__}", "bold cyan"),
        )
    )
    console.print()  # 与后续 REPL 提示之间留一个空行。
