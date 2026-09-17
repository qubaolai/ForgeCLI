"""/mode: 列出模式, 选一个 (ADR-0055 决策 1).

菜单只有一层, 列的是 `runtime.presets`: Bypass Permissions 只在启动时带了
`--allow-full-access` 才在里面. 没放开就不列, 而不是列出来再拒绝.
"""

from __future__ import annotations

from forgecli.core.permissions import (
    MODE_OPTIONS,
    ModeOption,
    ModeUnavailableError,
    PermissionMode,
    parse_permission_mode,
)
from forgecli.tui.chooser import Option, choose
from forgecli.tui.commands.context import CommandContext
from forgecli.tui.console import error, ok


def cmd_mode(context: CommandContext, argument: str) -> None:
    """`/mode <名字>` 直接切; 裸 `/mode` 弹菜单, Esc 退回提示符."""
    if not context.require_idle():
        return
    runtime = context.runtime
    raw = argument.strip()
    if raw:
        try:
            target = parse_permission_mode(raw)
        except ValueError:
            error(context.console, f"认不出这一档: {raw}")
            return
        _apply(context, target)
        return

    current = runtime.session.current().mode
    picked = choose(context.console, _options(runtime.presets), current=current.value)
    if picked is not None:
        _apply(context, PermissionMode(picked.key))


def _options(items: tuple[ModeOption, ...]) -> list[Option]:
    return [Option(item.value, item.label, item.hint) for item in items]


def mode_label(mode: PermissionMode) -> str:
    for option in MODE_OPTIONS:
        if option.value == mode.value:
            return option.label
    return mode.value


def _apply(context: CommandContext, target: PermissionMode) -> None:
    try:
        context.runtime.set_mode(target)
    except ModeUnavailableError as exc:
        # `/mode bypass_permissions` 没放开时走到这里: 参数形式绕过了菜单的过滤, 门在
        # transition_permission_mode 上, 三条入口都拦得住.
        error(context.console, exc.message)
        return
    except RuntimeError as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"模式: {mode_label(target)} ({target.value})")
