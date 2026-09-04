"""/mode: 隔离档 x 审批档.

两个轴独立 (domain/intents): 调隔离档不该顺手把审批档也改了. 菜单因此是三块 ——
整档预设, 隔离, 审批 —— 而不是一条从紧到松的线.
"""

from __future__ import annotations

from forgecli.domain.intents import (
    APPROVAL_OPTIONS,
    MODE_PRESETS,
    PRESET_NAMES,
    SANDBOX_OPTIONS,
    ApprovalPolicy,
    SandboxLevel,
    SessionMode,
    StanceOption,
    stance_label,
)
from forgecli.interfaces.tui.chooser import Option, choose
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import error, ok

_SECTIONS = (
    Option("preset", "整档预设", "一次设好两个轴"),
    Option("sandbox", "隔离", "围栏允许什么"),
    Option("approval", "审批", "什么时候要你点头"),
)


def cmd_mode(context: CommandContext, argument: str) -> None:
    """两级菜单; Esc 从轴退回分组, 再按一次退回提示符."""
    if not context.require_idle():
        return
    runtime = context.runtime
    raw = argument.strip()
    if raw:
        try:
            target = SessionMode.from_value(raw)
        except ValueError:
            error(context.console, f"认不出这一档: {raw}")
            return
        _apply(context, target)
        return

    while True:
        current = runtime.session.current().mode
        section = choose(context.console, list(_SECTIONS))
        if section is None:
            return
        if section.key == "preset":
            picked = choose(
                context.console, _options(MODE_PRESETS), current=_preset_name(current)
            )
            if picked is not None:
                _apply(context, PRESET_NAMES[picked.key])
        elif section.key == "sandbox":
            picked = choose(
                context.console,
                _options(SANDBOX_OPTIONS),
                current=current.sandbox.value,
            )
            if picked is not None:
                _apply(context, SessionMode(SandboxLevel(picked.key), current.approval))
        else:
            picked = choose(
                context.console,
                _options(APPROVAL_OPTIONS),
                current=current.approval.value,
            )
            if picked is not None:
                _apply(
                    context, SessionMode(current.sandbox, ApprovalPolicy(picked.key))
                )


def _options(items: tuple[StanceOption, ...]) -> list[Option]:
    return [Option(item.value, item.label, item.hint) for item in items]


def _preset_name(mode: SessionMode) -> str:
    return next((name for name, preset in PRESET_NAMES.items() if preset == mode), "")


def _apply(context: CommandContext, target: SessionMode) -> None:
    try:
        context.runtime.set_mode(target)
    except RuntimeError as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"模式: {stance_label(target)} ({target.value})")
