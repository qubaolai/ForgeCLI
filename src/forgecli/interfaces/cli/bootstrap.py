"""组合根：渲染 banner、解析目录信任，命中后装配并运行交互式会话。

启动顺序（ADR-0008 首启信任）：
    banner -> 解析当前目录信任 -> 命中/信任则进 REPL，拒绝/非 TTY 则提示后退出。
banner 先于信任解析渲染，保证即便因拒绝或非 TTY 直接退出，用户仍看到 banner。
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from forgecli.application.intent_router import IntentRouter
from forgecli.application.project import (
    ProjectContext,
    ProjectService,
    WorkspaceStartup,
)
from forgecli.application.session import SessionState
from forgecli.infrastructure.config import config_dir
from forgecli.infrastructure.project import (
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)
from forgecli.interfaces.cli.banner import render_banner
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import Repl
from forgecli.interfaces.cli.tty.tty import stdin_is_tty
from forgecli.interfaces.cli.tty_prompts import TtyDirectoryPicker, TtyTrustPrompter
from forgecli.interfaces.cli.wiring import build_registry


def _project_service() -> ProjectService:
    # 项目索引与项目配置都落在用户级 Forge home 下（projects/），不写入项目目录。
    projects = config_dir() / "projects"
    return ProjectService(
        TomlProjectIndexStore(projects / "index.toml"),
        TomlProjectConfigStore(projects),
    )


def run() -> None:
    """裸 forge 的产品入口。"""
    console = Console()
    render_banner(console=console)

    service = _project_service()
    startup = WorkspaceStartup(service, TtyTrustPrompter(console))
    result = startup.resolve(Path.cwd(), interactive=stdin_is_tty())
    if result.project is None:
        if result.reason == "declined":
            console.print("已取消：未信任当前目录，不创建任何配置。")
        else:  # no_tty
            console.print("[yellow]需要在终端(TTY)中确认是否信任当前目录。[/]")
        return

    context = ProjectContext(result.project)
    state = SessionState()
    output = RichOutput(console=console)
    presenter = RichMenuPresenter(console=console)
    picker = TtyDirectoryPicker(console=console)
    registry = build_registry(
        state=state,
        context=context,
        project_service=service,
        presenter=presenter,
        picker=picker,
        output=output,
    )
    router = IntentRouter(registry=registry)
    Repl(
        console=console,
        router=router,
        registry=registry,
        state=state,
        output=output,
    ).run()
