"""/mode: 列出预设, 选一个.

菜单只有一层, 只列预设 (ADR-0052): 隔离档与审批档两个轴仍是领域模型 (domain/intents),
但不再单独露给人调. 早先这里是"整档预设 / 隔离 / 审批"三块的两级菜单, 于是同一个问题
在两处能得到不同答案 —— 预设里选了 Auto, 再去审批那一块看到的却是"围栏内自动"这个
轴上的名字, 用户要自己把两套词对上.

列的是 `runtime.presets`, 不是 MODE_PRESETS 全表: Full Access 只在启动时带了
`--allow-full-access` 才在里面. 没放开就不列, 而不是列出来再拒绝.
"""

from __future__ import annotations

from forgecli.domain.intents import (
    PRESET_NAMES,
    ModeUnavailableError,
    SessionMode,
    StanceOption,
    stance_label,
)
from forgecli.interfaces.tui.chooser import Option, choose
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import error, ok


def cmd_mode(context: CommandContext, argument: str) -> None:
    """`/mode <预设名>` 直接切; 裸 `/mode` 弹菜单, Esc 退回提示符."""
    if not context.require_idle():
        return
    runtime = context.runtime
    raw = argument.strip()
    if raw:
        target = PRESET_NAMES.get(raw)
        if target is None:
            error(context.console, f"认不出这一档: {raw}")
            return
        _apply(context, target)
        return

    current = runtime.session.current().mode
    picked = choose(
        context.console, _options(runtime.presets), current=_preset_name(current)
    )
    if picked is not None:
        _apply(context, PRESET_NAMES[picked.key])


def _options(items: tuple[StanceOption, ...]) -> list[Option]:
    return [Option(item.value, item.label, item.hint) for item in items]


def _preset_name(mode: SessionMode) -> str:
    return next((name for name, preset in PRESET_NAMES.items() if preset == mode), "")


def _apply(context: CommandContext, target: SessionMode) -> None:
    try:
        context.runtime.set_mode(target)
    except ModeUnavailableError as exc:
        # `/mode full_access` 没放开时走到这里: 参数形式绕过了菜单的过滤, 门在
        # ProjectRuntime.set_mode 上, 三条入口都拦得住.
        error(context.console, exc.message)
        return
    except RuntimeError as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"模式: {stance_label(target)} ({target.value})")
