"""ForgeCLI 的 Typer 命令行入口。

本模块只负责根入口、全局选项和入口分发。产品入口收敛为裸 ``forge``
进入交互式会话；具体能力通过 REPL 内的 slash command 提供。
"""

from __future__ import annotations

import typer
from rich.console import Console

from forgecli.interfaces.cli.bootstrap import build_repl
from forgecli.shared import __version__

app = typer.Typer(
    name="forge",
    help="Forge —— 一个可在终端交互的 AI 编码助手。",
    # MVP 阶段暂不生成 shell 补全脚本，减少安装与验收变量。
    add_completion=False,
    # 裸 forge 是产品主入口：不显示 help，而是进入交互式 workspace 会话。
    no_args_is_help=False,
)

# 模块级 Console 由 CLI 层共享；业务层不要直接依赖 Rich。
console = Console()


def _version_callback(vla: bool) -> None:
    """处理 ``--version`` 的 eager 回调。

    Typer 在解析到 eager option 后会先调用这里。打印版本并抛出
    ``typer.Exit`` 可以阻止裸 ``forge --version`` 继续进入 REPL。
    """
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
        help="显示版本号.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Forge 命令行根回调。

    ``invoke_without_command=True`` 让裸 ``forge`` 也会执行回调。MVP 当前
    不注册 Typer 子命令，避免形成交互式和命令式两套入口。
    """
    if ctx.invoked_subcommand is None:
        build_repl().run()


def main() -> None:
    """Poetry console script 入口。"""
    app()
