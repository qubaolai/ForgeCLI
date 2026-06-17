"""可安全退出的 REPL stub"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from forgecli.interfaces.cli.banner import render_banner

console = Console()

_EXIT_WORDS = {"exit", "quit", ":q"}


def start_repl() -> None:
    """交互式会话入口(stub)"""
    render_banner(console=console)
    console.print(
        Panel.fit(
            "进入 Forge 交互式会话(stub)。\n"
            "输入 [bold]exit[/] 或按 [bold]Ctrl-C[/] 退出。",
            border_style="cyan",
        )
    )
    while True:
        try:
            line = console.input("[bold green]forge[/] › ")
        except (EOFError, KeyboardInterrupt):
            # Ctrl-D / Ctrl-C 都能安全退出,不抛栈
            console.print("\n再见。")
            break

        text = line.strip()
        if not text:
            continue
        if text.lower() in _EXIT_WORDS:
            console.print("bye bye")
            break

        # stub:暂时只回显,后续才接入真正的处理逻辑
        console.print(f"[dim](还没有实现)你说了: {text}[/]")
