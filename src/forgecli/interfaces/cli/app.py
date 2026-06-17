"""cli入口"""

from __future__ import annotations

import typer
from rich.console import Console

from forgecli.interfaces.cli import __version__
from forgecli.interfaces.cli.repl import start_repl

app = typer.Typer(
    name="forge",
    help="Forge —— 一个可在终端交互的 AI 编码助手。",
    # todo 暂时不引入shell补全
    add_completion=False,
    # 裸 forge 不显示 help,而是进 REPL
    no_args_is_help=False,
)

console = Console()


def _version_callback(vla: bool) -> None:
    """--version 的 eager 回调:打印版本后立即退出,不再走后续逻辑。"""
    if vla:
        console.print(f"forge [bold cyan]{__version__}[/]")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        "-v",
        help="显示版本号.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Forge 命令行入口。不带子命令时进入交互式会话(REPL)。"""
    # 只有在没有任何子命令时,裸 forge 才进 REPL
    if ctx.invoked_subcommand is None:
        start_repl()


@app.command()
def chat() -> None:
    """启动一次对话会话(占位)。"""
    console.print("[yellow]chat 命令尚未实现。[/]")


@app.command()
def status() -> None:
    """查看当前运行状态(占位)。"""
    console.print("[yellow]status 命令尚未实现。[/]")


def main() -> None:
    """console script 入口。"""
    app()
