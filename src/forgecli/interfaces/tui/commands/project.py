"""/projects: 项目中心.

同一时刻只激活一个项目, 项目锁与单活动 turn 的约束不变 (ADR-0025 决策 8): 抢不到
锁的一方看到的是占用者的 pid, 而不是一个含糊的失败.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from forgecli.domain.workspace.project import WorkspaceError
from forgecli.infrastructure.project import ProjectLockedError
from forgecli.interfaces.tui.chooser import Option, ask_text, choose
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import (
    STYLE_DIM,
    display_path,
    error,
    listing,
    ok,
    rule,
    shorten_path,
)


def cmd_projects(context: CommandContext, argument: str) -> None:
    projects = context.registry.projects
    raw = argument.strip()
    if raw:
        _trust_and_activate(context, raw)
        return
    active = context.registry.active
    active_id = None if active is None else active.project.project_id
    trusted = projects.list_trusted()
    rule(context.console, "项目")
    if trusted:
        context.console.print(
            listing(
                ("项目", "主工作区", "额外目录"),
                (
                    (
                        item.project_id
                        + (" ●" if item.project_id == active_id else ""),
                        display_path(item.primary_workspace_root),
                        str(len(item.workspace_roots) - 1),
                    )
                    for item in trusted
                ),
            )
        )
    else:
        context.console.print(Text("还没有信任过任何目录", style=STYLE_DIM))
    options = [
        *(
            Option(
                item.project_id,
                shorten_path(item.primary_workspace_root),
                item.project_id,
            )
            for item in trusted
        ),
        Option("__trust__", "信任一个新目录", "把它作为项目打开"),
    ]
    picked = choose(context.console, options, current=active_id or "")
    if picked is None:
        return
    if picked.key == "__trust__":
        path = ask_text(context.console, "目录路径", default=str(Path.cwd()))
        if path:
            _trust_and_activate(context, path)
        return
    _activate(context, picked.key)


def _trust_and_activate(context: CommandContext, raw: str) -> None:
    projects = context.registry.projects
    try:
        path = projects.normalize_workspace_dir(raw, Path.cwd())
    except WorkspaceError as exc:
        error(context.console, exc.message)
        return
    existing = projects.find_trusted(path)
    project = existing or projects.trust(path)
    _activate(context, project.project_id)


def _activate(context: CommandContext, project_id: str) -> None:
    active = context.registry.active
    if active is not None and active.busy:
        error(context.console, "当前项目还有 turn 在跑, 先等它结束")
        return
    try:
        runtime = context.registry.activate(project_id)
    except KeyError:
        error(context.console, f"项目不存在: {project_id}")
        return
    except ProjectLockedError as exc:
        error(context.console, exc.message)
        return
    except RuntimeError as exc:
        error(context.console, str(exc))
        return
    ok(
        context.console,
        f"当前项目: {shorten_path(runtime.project.primary_workspace_root)}",
    )
