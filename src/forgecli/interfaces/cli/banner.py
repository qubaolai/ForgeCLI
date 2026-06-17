from __future__ import annotations

from rich.console import Console
from rich.text import Text

from forgecli.interfaces.cli import __version__

# ANSI Shadow 字体的 "FORGE"
_LOGO = r"""
███████╗ ██████╗ ██████╗  ██████╗ ███████╗
██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
█████╗  ██║   ██║██████╔╝██║  ███╗█████╗  
██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  
██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
"""

# 自上而下的火焰色:亮黄 → 橙 → 暗红
_FLAME = ["bright_yellow", "yellow", "orange1", "dark_orange", "orange_red1", "red3"]

_TAGLINE = "在终端里锻造代码的 AI 助手"


def render_banner(console: Console) -> None:
    """打印 Forge 启动 banner(纯渲染,无业务逻辑)。"""
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
    console.print()  # 末尾留一个空行
