"""/rewind: 回到某条 assistant 消息之前的文件状态 (ADR-0054 决策 5).

文件历史只记 Edit / Write 改过的文件, Bash 改的不在里面.
"""

from __future__ import annotations

from rich.text import Text

from forgecli.tui.chooser import Option, choose, confirm
from forgecli.tui.commands.context import CommandContext
from forgecli.tui.console import STYLE_DIM, error, listing, ok, rule, warn


def cmd_rewind(context: CommandContext, argument: str) -> None:
    if not context.require_idle():
        return
    runtime = context.runtime
    snapshots = runtime.rewind_points()
    if not snapshots:
        context.console.print(Text("还没有可回退的快照", style=STYLE_DIM))
        return
    raw = argument.strip()
    if raw:
        _rewind(context, raw)
        return
    rule(context.console, "文件历史")
    context.console.print(
        listing(
            ("消息", "创建于", "文件数"),
            (
                (item.message_id, item.created_at, str(len(item.backups)))
                for item in snapshots
            ),
        )
    )
    picked = choose(
        context.console,
        [
            Option(
                item.message_id,
                item.message_id,
                f"{item.created_at} · {len(item.backups)} 个文件",
            )
            for item in snapshots
        ],
    )
    if picked is not None:
        _rewind(context, picked.key)


def _rewind(context: CommandContext, message_id: str) -> None:
    runtime = context.runtime
    if not runtime.tools.file_history.can_restore(message_id):
        error(context.console, f"没有这条消息的快照: {message_id}")
        return
    if not confirm(context.console, "回退会覆盖这些文件此后的全部改动, 继续?"):
        return
    try:
        outcome = runtime.rewind(message_id)
    except RuntimeError as exc:
        error(context.console, str(exc))
        return
    ok(
        context.console,
        f"已回退: 还原 {len(outcome.restored)} 个, 删除 {len(outcome.removed)} 个",
    )
    if outcome.failed:
        warn(context.console, "有文件没能还原: " + ", ".join(outcome.failed))
