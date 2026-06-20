"""ForgeCLI 的 Typer 命令行入口。

本模块只负责命令注册、全局选项和入口分发。真正的交互式会话逻辑
放在 ``repl`` 模块，避免 CLI 框架细节渗透到后续应用层服务。
"""

from __future__ import annotations

import typer
from rich.console import Console

from forgecli.interfaces.cli.repl import start_repl
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

    ``invoke_without_command=True`` 让裸 ``forge`` 也会执行回调；只有没有
    子命令被匹配时才启动 REPL，避免 ``forge chat`` 等命令被重复处理。
    """
    # 仅裸 forge 进入 REPL；带子命令时由 Typer 分派给对应 command。
    if ctx.invoked_subcommand is None:
        start_repl()


@app.command()
def chat() -> None:
    """启动一次对话会话。

    当前仍是占位命令，用于先固定 CLI 形状和 smoke test；后续应复用裸
    ``forge`` 的会话服务，而不是实现另一套对话流程。
    """
    console.print("[yellow]chat 命令尚未实现。[/]")


@app.command()
def status() -> None:
    """查看当前运行状态。

    当前仍是占位命令；真正状态查询会在应用层会话与存储服务落地后接入。
    """
    console.print("[yellow]status 命令尚未实现。[/]")


def main() -> None:
    """Poetry console script 入口。"""
    app()
