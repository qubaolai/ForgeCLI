"""Forge 的极薄命令行启动入口。

裸 ``forge`` 启动仅监听本机的 Web 控制面；业务交互全部进入浏览器。
"""

from __future__ import annotations

import typer
from rich.console import Console

from forgecli.interfaces.web.server import DEFAULT_PORT
from forgecli.interfaces.web.server import run as run_web
from forgecli.shared import __version__

app = typer.Typer(
    name="forge",
    help="Forge —— 本地优先的 Web AI 编码助手。",
    # MVP 阶段暂不生成 shell 补全脚本，减少安装与验收变量。
    add_completion=False,
    # 裸 forge 是产品主入口：不显示 help，而是启动本地 Web 控制面。
    no_args_is_help=False,
)

# 模块级 Console 由 CLI 层共享；业务层不要直接依赖 Rich。
console = Console()


def _version_callback(vla: bool) -> None:
    """处理 ``--version`` 的 eager 回调。

    Typer 在解析到 eager option 后会先调用这里。打印版本并抛出
    ``typer.Exit`` 可以阻止裸 ``forge --version`` 继续启动服务。
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
    open_browser: bool = typer.Option(
        False,
        "--open",
        help="启动后自动在默认浏览器中打开控制面；默认只打印启动链接。",
    ),
    port: int = typer.Option(
        DEFAULT_PORT,
        "--port",
        min=0,
        max=65535,
        help="本地监听端口；固定端口才能让已打开的页面在重启后自己连回来，0 表示随机。",
    ),
) -> None:
    """Forge 命令行根回调。

    ``invoke_without_command=True`` 让裸 ``forge`` 也会执行回调。CLI 只保留启动与
    诊断参数，不再形成第二套业务入口。
    """
    if ctx.invoked_subcommand is None:
        # 服务没能开始时以非零码退出，让脚本与 CI 可判据；正常停止返回 0。
        code = run_web(port=port, open_browser=open_browser)
        if code != 0:
            raise typer.Exit(code)


def main() -> None:
    """Poetry console script 入口。"""
    app()
