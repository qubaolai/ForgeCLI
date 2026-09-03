"""/checkpoints, /undo, /recovery: 恢复点的列出, 预览与还原."""

from __future__ import annotations

from rich.text import Text

from forgecli.interfaces.tui.chooser import Option, choose, confirm
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import (
    STYLE_DIM,
    STYLE_WARN,
    error,
    kv_table,
    listing,
    ok,
    rule,
    truncate,
    warn,
)


def cmd_checkpoints(context: CommandContext, argument: str) -> None:
    runtime = context.runtime
    checkpoints = runtime.list_checkpoints()
    if not checkpoints:
        context.console.print(Text("还没有恢复点", style=STYLE_DIM))
        return
    raw = argument.strip()
    if raw:
        _restore(context, raw)
        return
    rule(context.console, "恢复点")
    context.console.print(
        listing(
            ("恢复点", "创建于", "状态", "策略", "改动"),
            (
                (
                    item.checkpoint_id,
                    item.created_at,
                    item.status.value,
                    item.snapshot_strategy.value,
                    str(len(item.mutations.entries)),
                )
                for item in checkpoints
            ),
        )
    )
    picked = choose(
        context.console,
        "预览哪一个",
        [
            Option(
                item.checkpoint_id,
                item.checkpoint_id,
                f"{item.created_at} · {len(item.mutations.entries)} 处改动",
            )
            for item in checkpoints
        ],
    )
    if picked is not None:
        _restore(context, picked.key)


def cmd_undo(context: CommandContext, _argument: str) -> None:
    """还原最近一次, 与 Web 的撤销按钮同一个动作: 取列表第一条."""
    runtime = context.runtime
    checkpoints = runtime.list_checkpoints()
    if not checkpoints:
        warn(context.console, "没有可撤销的操作")
        return
    _restore(context, checkpoints[0].checkpoint_id)


def cmd_recovery(context: CommandContext, _argument: str) -> None:
    """恢复层状态: 恢复点总数与未收尾的事务."""
    runtime = context.runtime
    status = runtime.recovery_status()
    rule(context.console, "恢复层")
    context.console.print(
        kv_table([("恢复点", str(status.get("checkpoint_count", 0)))])
    )
    pending = status.get("pending")
    if isinstance(pending, list) and pending:
        context.console.print(
            Text(f"有 {len(pending)} 个未收尾的事务", style=STYLE_WARN)
        )
        for item in pending:
            context.console.print(
                Text(f"  {truncate(str(item), 110)}", style=STYLE_DIM)
            )


def _restore(context: CommandContext, checkpoint_id: str) -> None:
    if not context.require_idle():
        return
    runtime = context.runtime
    checkpoint = runtime.checkpoint(checkpoint_id)
    if checkpoint is None:
        error(context.console, f"恢复点不存在: {checkpoint_id}")
        return
    preview = runtime.tools.recovery.preview(
        checkpoint, runtime.tools.context_factory()
    )
    rule(context.console, f"预览 {checkpoint.checkpoint_id}")
    context.console.print(
        listing(
            ("路径", "动作", "冲突", "说明"),
            (
                (item.relative_path, item.action, item.conflict.value, item.detail)
                for item in preview.items
            ),
        )
    )
    conflicted = preview.conflicted
    if conflicted:
        # 冲突项默认跳过, 不静默覆盖: 恢复点建立之后有人又改过这些文件, 覆盖等于把
        # 那次改动一起抹掉, 而它不在任何恢复点里.
        context.console.print(
            Text(f"{len(conflicted)} 处与当前工作区冲突, 默认跳过", style=STYLE_WARN)
        )
    if not confirm(context.console, f"还原 {checkpoint.checkpoint_id}?"):
        return
    force = bool(conflicted) and confirm(
        context.console, "连冲突项一起覆盖?", default=False
    )
    outcome = runtime.tools.recovery.restore(
        checkpoint, runtime.tools.context_factory(), force_conflicts=force
    )
    ok(
        context.console,
        f"已还原 {len(outcome.restored)} 项, 跳过 {len(outcome.skipped)} 项"
        + (
            f"; 新恢复点 {outcome.new_checkpoint_id}"
            if outcome.new_checkpoint_id
            else ""
        ),
    )
