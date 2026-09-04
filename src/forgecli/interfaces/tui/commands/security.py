"""/tools, /rules, /dirs: 模型能看到什么工具, 学过哪些放行, 能碰哪些目录."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from forgecli.application.security.workspace_grants import GrantError
from forgecli.domain.workspace.project import WorkspaceError
from forgecli.interfaces.tui.chooser import Option, ask_text, choose, confirm
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import (
    STYLE_DIM,
    error,
    listing,
    ok,
    rule,
    shorten_path,
)


def cmd_tools(context: CommandContext, _argument: str) -> None:
    """两份清单, 因为有两个问题: 这一档模式下模型看得见什么, 以及一共注册了什么."""
    runtime = context.runtime
    mode = runtime.session.current().mode
    catalog = runtime.tools.dispatcher.catalog_for(mode)
    visible = {spec.name for spec in catalog.entries}
    rule(context.console, f"工具 · {mode.value}")
    context.console.print(
        listing(
            ("工具", "名称", "本档可见", "能力"),
            (
                (
                    spec.name,
                    spec.title,
                    "是" if spec.name in visible else "否",
                    ", ".join(
                        sorted(item.value for item in spec.declared_capabilities)
                    ),
                )
                for spec in runtime.tools.registry.describe_all()
            ),
        )
    )


def cmd_rules(context: CommandContext, argument: str) -> None:
    """人类明确选过的受限放行规则. 模型不得创建, 扩大或选择它们."""
    runtime = context.runtime
    if argument.strip() == "prune":
        removed = runtime.tools.learned.prune()
        ok(context.console, f"清理了 {removed} 条失效规则")
        return
    rules = [
        rule_item
        for rule_item in runtime.tools.learned.rules
        if rule_item.match.workspace_id == runtime.tools.workspace_id
    ]
    if not rules:
        context.console.print(Text("这个工作区还没有学过的放行规则", style=STYLE_DIM))
        return
    rule(context.console, "学习到的放行规则")
    context.console.print(
        listing(
            ("规则", "范围", "工具", "标签", "状态"),
            (
                (
                    item.rule_id,
                    item.scope.value,
                    item.match.tool_name,
                    item.label,
                    "已撤销" if item.revoked else "生效",
                )
                for item in rules
            ),
        )
    )
    actions = [
        *(
            Option(item.rule_id, f"撤销 {item.label or item.rule_id}", item.scope.value)
            for item in rules
            if not item.revoked
        ),
        Option("__prune__", "清理失效规则", "过期或不再匹配当前画像的"),
    ]
    picked = choose(context.console, actions)
    if picked is None:
        return
    if picked.key == "__prune__":
        ok(context.console, f"清理了 {runtime.tools.learned.prune()} 条失效规则")
        return
    if runtime.tools.learned.revoke(picked.key):
        ok(context.console, f"已撤销 {picked.key}")
    else:
        error(context.console, "规则不存在")


def cmd_dirs(context: CommandContext, argument: str) -> None:
    """额外工作区目录.

    重启后额外目录一律按只读恢复 (ADR-0025 决策 11): 项目配置只持久化路径, 不持久化
    写授权. 想要写就在这里重新升一次 —— 安全上宁可多问一次.
    """
    if not context.require_idle():
        return
    runtime = context.runtime
    raw = argument.strip()
    if raw:
        _add(context, raw, write=False)
        return
    rule(context.console, "工作区目录")
    context.console.print(
        listing(
            ("目录", "权限", "角色"),
            (
                (
                    root,
                    _access(context, root),
                    "主工作区" if index == 0 else "额外目录",
                )
                for index, root in enumerate(runtime.project.workspace_roots)
            ),
        )
    )
    extra = runtime.project.workspace_roots[1:]
    actions = [
        Option("__add__", "添加目录", "默认只读, 可选可写"),
        *(Option(f"grant:{root}", f"改权限 {shorten_path(root)}") for root in extra),
        *(Option(f"remove:{root}", f"移除 {shorten_path(root)}") for root in extra),
    ]
    picked = choose(context.console, actions)
    if picked is None:
        return
    if picked.key == "__add__":
        path = ask_text(context.console, "目录路径")
        if not path:
            return
        _add(context, path, write=confirm(context.console, "允许写入?"))
        return
    action, _, root = picked.key.partition(":")
    if action == "grant":
        _add(context, root, write=confirm(context.console, "允许写入?"))
        return
    _remove(context, root)


def _access(context: CommandContext, root: str) -> str:
    runtime = context.runtime
    if root == runtime.project.primary_workspace_root:
        return "write"
    grant = runtime.tools.grants.access_for(root)
    return "read" if grant is None else grant.value


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
    except (GrantError, WorkspaceError) as exc:
        error(context.console, getattr(exc, "message", str(exc)))
        return
    ok(context.console, f"{shorten_path(str(path))} · {'write' if write else 'read'}")


def _remove(context: CommandContext, raw: str) -> None:
    runtime = context.runtime
    projects = context.registry.projects
    try:
        path = projects.normalize_workspace_dir(
            raw, Path(runtime.project.primary_workspace_root)
        )
        runtime.project = projects.remove_workspace_dir(runtime.project, path)
        runtime.revoke_workspace(path)
    except (GrantError, WorkspaceError) as exc:
        error(context.console, getattr(exc, "message", str(exc)))
        return
    ok(context.console, f"已移除 {shorten_path(str(path))}")
