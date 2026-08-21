"""Forge 的极薄命令行启动入口。

两条启动路径，同一套业务组合根（ADR-0025 决策 3，组合根在 ``interfaces/runtime``）：

- 裸 ``forge``：监听本机 Web 控制面，业务交互进浏览器。这是产品主入口（ADR-0025
  决策 1），默认不变。
- ``forge cli``：在当前终端进入交互式会话，走 ``bootstrap.run()``。

**两者不能同时对同一项目运行。** CLI 与 Web 都要取项目级 ``forge.lock``（ADR-0025
§2.3）：计划、会话与恢复点都不加文件锁，正是因为这把进程锁保证了同一项目只有一个
Forge 进程——放行第二个等于默认丢写。后启动的一方报 ``PROJECT_LOCKED`` 退出，
两条路径用同一个退出码。

这里只做入口分发，不放任何业务判断：哪条路径都不该成为第二套业务入口。
"""

from __future__ import annotations

import typer
from rich.console import Console

from forgecli.interfaces.cli.bootstrap import run as run_session
from forgecli.interfaces.exit_codes import ExitCode
from forgecli.interfaces.web.server import DEFAULT_PORT
from forgecli.interfaces.web.server import run as run_web
from forgecli.shared import __version__

app = typer.Typer(
    name="forge",
    help="Forge —— 本地优先的 AI 编码助手：裸 forge 起 Web 控制面，forge cli 进终端。",
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
        help="[仅裸 forge] 启动后自动在默认浏览器中打开控制面；默认只打印启动链接。",
    ),
    port: int = typer.Option(
        DEFAULT_PORT,
        "--port",
        min=0,
        max=65535,
        help=(
            "[仅裸 forge] 本地监听端口；固定端口才能让已打开的页面在重启后自己连"
            "回来，0 表示随机。"
        ),
    ),
) -> None:
    """Forge 命令行根回调。

    ``invoke_without_command=True`` 让裸 ``forge`` 也会执行回调；带子命令时这里只
    解析全局选项，由子命令自己决定做什么。CLI 只保留启动与诊断参数，不再形成第二套
    业务入口。
    """
    if ctx.invoked_subcommand is not None:
        return
    # 服务没能开始时以非零码退出，让脚本与 CI 可判据；正常停止返回 0。
    code = run_web(port=port, open_browser=open_browser)
    if code != 0:
        raise typer.Exit(code)


@app.command("cli")
def _cli() -> None:
    """在当前终端进入交互式会话（不启动 Web 控制面）。

    需要真终端：没有 TTY 时直接以 ``NO_TTY`` 退出，而不是降级成一个读不到输入的
    循环。同一项目已有 Forge 在跑时以 ``PROJECT_LOCKED`` 退出并打印占用者 pid。
    """
    code = run_session()
    if code is not ExitCode.OK:
        raise typer.Exit(int(code))


def main() -> None:
    """Poetry console script 入口。"""
    app()
