"""/tools, /permissions, /dirs: 模型能看到什么工具, 有哪些规则, 能碰哪些目录."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from forgecli.core.permissions import PermissionRuleSource
from forgecli.core.tool.tool import PromptOptions
from forgecli.project.model import WorkspaceError
from forgecli.tui.chooser import Option, ask_text, choose, confirm
from forgecli.tui.commands.context import CommandContext
from forgecli.tui.console import (
    STYLE_DIM,
    display_path,
    error,
    listing,
    ok,
    rule,
    shorten_path,
)

# 来源在界面上的名字.
_SOURCE_LABELS = {
    PermissionRuleSource.POLICY_SETTINGS.value: "内置 (只读)",
    PermissionRuleSource.USER_SETTINGS.value: "用户 config.json",
    PermissionRuleSource.PROJECT_SETTINGS.value: "项目 forge.json",
    PermissionRuleSource.CLI_ARG.value: "命令行",
    PermissionRuleSource.SESSION.value: "本会话",
}


def cmd_tools(context: CommandContext, _argument: str) -> None:
    """两份清单, 因为有两个问题: 现在模型看得见什么, 以及一共注册了什么."""
    runtime = context.runtime
    runner = runtime.tools.runner
    visible = {tool.name for tool in runner.pool()}
    rule(context.console, f"工具 · {runtime.session.current().mode.value}")
    options = PromptOptions(tools=runner.pool(), get_app_state=runner.get_app_state)
    context.console.print(
        listing(
            ("工具", "说明", "在池里", "只读"),
            (
                (
                    tool.name,
                    tool.prompt(options).strip().splitlines()[0][:60],
                    "是" if tool.name in visible else "否 (deny 规则)",
                    "是" if tool.is_read_only({}) else "否",
                )
                for tool in runner.base_tools
            ),
        )
    )


def cmd_permissions(context: CommandContext, _argument: str) -> None:
    """三张规则表按来源列出; 能删的只有用户, 项目与本会话来源的."""
    runtime = context.runtime
    tables = runtime.permission_rules()
    rows: list[tuple[str, str, str]] = []
    removable: list[Option] = []
    for behavior, by_source in tables.items():
        for source, rules in by_source.items():
            for text in rules:
                rows.append((behavior, _SOURCE_LABELS.get(source, source), text))
                if source != PermissionRuleSource.POLICY_SETTINGS.value:
                    removable.append(
                        Option(f"{behavior}|{source}|{text}", f"删除 {text}", behavior)
                    )
    if not rows:
        context.console.print(Text("没有任何权限规则", style=STYLE_DIM))
        return
    rule(context.console, "权限规则")
    context.console.print(listing(("行为", "来源", "规则"), rows))
    if not removable:
        return
    picked = choose(context.console, removable)
    if picked is None:
        return
    behavior, source, text = picked.key.split("|", 2)
    if runtime.remove_permission_rule(behavior, source, text):
        ok(context.console, f"已删除 {text}")
    else:
        error(context.console, "规则不存在或不可删除")


def cmd_dirs(context: CommandContext, argument: str) -> None:
    """额外工作目录: 加进来的目录读写都自动放行."""
    if not context.require_idle():
        return
    runtime = context.runtime
    raw = argument.strip()
    if raw:
        _add(context, raw, write=True)
        return
    rule(context.console, "工作区目录")
    context.console.print(
        listing(
            ("目录", "角色"),
            (
                (display_path(root), "主工作区" if index == 0 else "额外目录")
                for index, root in enumerate(runtime.tools.working_directories())
            ),
        )
    )
    extra = runtime.tools.working_directories()[1:]
    actions = [
        Option("__add__", "添加目录"),
        *(Option(f"remove:{root}", f"移除 {shorten_path(root)}") for root in extra),
    ]
    picked = choose(context.console, actions)
    if picked is None:
        return
    if picked.key == "__add__":
        path = ask_text(context.console, "目录路径")
        if not path:
            return
        _add(context, path, write=confirm(context.console, "确认加入?"))
        return
    _, _, root = picked.key.partition(":")
    _remove(context, root)


def _add(context: CommandContext, raw: str, *, write: bool) -> None:
    runtime = context.runtime
    projects = context.registry.projects
    try:
        path = projects.normalize_workspace_dir(
            raw, Path(runtime.project.primary_workspace_root)
        )
        if str(path) != runtime.project.primary_workspace_root:
            runtime.grant_workspace(path, write=write)
        runtime.project = projects.add_workspace_dir(runtime.project, path)
    except WorkspaceError as exc:
        error(context.console, getattr(exc, "message", str(exc)))
        return
    ok(context.console, f"已加入 {shorten_path(str(path))}")


def _remove(context: CommandContext, raw: str) -> None:
    runtime = context.runtime
    projects = context.registry.projects
    try:
        path = projects.normalize_workspace_dir(
            raw, Path(runtime.project.primary_workspace_root)
        )
        runtime.project = projects.remove_workspace_dir(runtime.project, path)
        runtime.revoke_workspace(path)
    except WorkspaceError as exc:
        error(context.console, getattr(exc, "message", str(exc)))
        return
    ok(context.console, f"已移除 {shorten_path(str(path))}")
