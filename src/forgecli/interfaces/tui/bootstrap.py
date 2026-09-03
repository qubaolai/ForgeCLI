"""``forge --cli`` 的启动路径.

与 ``interfaces/web/server.py`` 对称: 装配同一个 ``ProjectRuntimeRegistry``, 拿同一把
项目锁, 用同一套退出码. 差别只有一处 —— 交互在这个进程的终端里, 不在浏览器里.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

from forgecli.application.project.project_service import ProjectService
from forgecli.domain.workspace.project import ProjectConfig
from forgecli.infrastructure.project import ProjectLockedError
from forgecli.interfaces.exit_codes import ExitCode
from forgecli.interfaces.runtime.logging_wiring import start_observability
from forgecli.interfaces.runtime.project_runtime import (
    ProjectRuntimeRegistry,
    build_project_service,
)
from forgecli.interfaces.tui.chooser import confirm
from forgecli.interfaces.tui.console import STYLE_DIM, make_console
from forgecli.interfaces.tui.session_app import SessionApp
from forgecli.shared import __version__
from forgecli.shared.observability.log import get_log

_log = get_log(__name__)


def run() -> int:
    """跑一个终端会话, 返回进程退出码."""
    console = make_console()
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        # 审批, 菜单和计划评审都要读人的输入. 没有终端就没有人能回答, 而一个读不到
        # 输入的会话只会静静挂在那里等 —— 说清楚比等下去好.
        console.print(
            "[red]forge --cli 需要一个交互式终端[/]: "
            "stdin 或 stdout 不是 TTY. 裸 forge 启动 Web 控制面, 那条路不需要 TTY."
        )
        return ExitCode.NO_TTY

    status = start_observability()
    _log.info(
        "forge.start",
        entry="cli",
        version=__version__,
        cwd=str(Path.cwd()),
        log_file=None if status.file is None else str(status.file),
        log_level=status.level,
    )
    projects = build_project_service()
    registry = ProjectRuntimeRegistry(projects)
    try:
        project = _resolve_project(console, projects)
        if project is not None:
            try:
                registry.activate(project.project_id)
            except ProjectLockedError as exc:
                # 与 Web 同一个码: "另一个 Forge 在跑"与对方是哪种入口无关.
                _log.warning(
                    "forge.refused", reason="project_locked", message=exc.message
                )
                console.print(f"[yellow]{exc.message}[/]")
                return ExitCode.PROJECT_LOCKED
        code = SessionApp(console, registry).run()
        _log.info("forge.stop", entry="cli")
        return code
    finally:
        registry.close()


def _resolve_project(
    console: Console, projects: ProjectService
) -> ProjectConfig | None:
    """当前目录已信任就直接打开, 否则问一次.

    问而不是自动信任: 信任一个目录等于把它交给模型读写, 那个决定只能由人自己做
    (ADR-0008). 拒绝也不是错误 —— 会话照常起来, 用 /projects 换一个就是.
    """
    cwd = Path.cwd()
    existing = projects.find_trusted(cwd)
    if existing is not None:
        return existing
    console.print(f"[{STYLE_DIM}]当前目录还没有作为 Forge 项目打开过: {cwd}[/]")
    if not confirm(console, "信任这个目录并作为项目打开?"):
        console.print(f"[{STYLE_DIM}]没有激活项目; 用 /projects 选一个.[/]")
        return None
    return projects.trust(cwd)
