"""Forge 的极薄命令行启动入口。

只有一条启动路径: 裸 ``forge`` 监听本机 Web 控制面, 业务交互进浏览器 (ADR-0025
决策 1). 组合根在 ``interfaces/runtime``, 这里只负责解析启动参数并把进程交出去,
不放任何业务判断。

同一项目只能有一个 Forge 进程: 计划、会话与恢复点都不加文件锁, 正是因为项目级
``forge.lock`` 保证了这一点 (ADR-0025 §2.3)。抢不到锁的一方报 ``PROJECT_LOCKED``
退出。退出码住在 ``interfaces/exit_codes``, 那是进程级契约。
"""

from __future__ import annotations

import typer
from rich.console import Console

from forgecli.interfaces.web.server import DEFAULT_PORT
from forgecli.interfaces.web.server import run as run_web
from forgecli.shared import __version__

app = typer.Typer(
    name="forge",
    help="Forge —— 本地优先的 AI 编码助手：启动本机 Web 控制面。",
    # MVP 阶段暂不生成 shell 补全脚本，减少安装与验收变量。
    add_completion=False,
    # 裸 forge 是唯一入口：不显示 help，而是启动本地 Web 控制面。
    no_args_is_help=False,
)

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
        help=(
            "本地监听端口；固定端口才能让已打开的页面在重启后自己连回来，"
            "0 表示随机。"
        ),
    ),
) -> None:
    """Forge 命令行根回调。

    ``invoke_without_command=True`` 让裸 ``forge`` 也会执行回调。没有子命令 ——
    终端交互入口由 ADR-0025 决策 1 取消, 配置面全部在 Web。
    """
    # 服务没能开始时以非零码退出，让脚本与 CI 可判据；正常停止返回 0。
    code = run_web(port=port, open_browser=open_browser)
    if code != 0:
        raise typer.Exit(code)


def main() -> None:
    """Poetry console script 入口。"""
    app()
