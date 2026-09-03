"""/plan, /todo: 计划目录, 活动计划正文与待办清单.

正文直接读计划文件, 不用待办状态反过来重组 (ADR-0025 决策 15): 两个来源迟早会不一致,
而计划文件才是真相源.
"""

from __future__ import annotations

from rich.markdown import Markdown
from rich.text import Text

from forgecli.domain.planning.todo import TodoStatus
from forgecli.interfaces.tui.chooser import Option, choose
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import (
    STYLE_DIM,
    error,
    listing,
    ok,
    rule,
    truncate,
)
from forgecli.interfaces.tui.plan_review import review

_STATUS_MARKS = {
    TodoStatus.PENDING: "○",
    TodoStatus.IN_PROGRESS: "◐",
    TodoStatus.DONE: "●",
}


def cmd_plan(context: CommandContext, argument: str) -> None:
    runtime = context.runtime
    raw = argument.strip()
    if raw == "review":
        review(context.console, runtime)
        return
    index = runtime.plan_index()
    if not index.plans:
        context.console.print(Text("这个项目还没有计划", style=STYLE_DIM))
        return
    if raw:
        _activate(context, raw)
        return
    rule(context.console, "计划目录")
    context.console.print(
        listing(
            ("计划", "状态", "版本", "更新于", "标题"),
            (
                (
                    item.plan_id
                    + (" ●" if item.plan_id == index.active_plan_id else ""),
                    item.status.value,
                    f"r{item.revision}",
                    item.updated_at,
                    truncate(item.title, 48),
                )
                for item in index.plans
            ),
        )
    )
    markdown = runtime.tools.planning.read_plan()
    if markdown:
        rule(context.console, "活动计划")
        context.console.print(Markdown(markdown))
    picked = choose(
        context.console,
        "切换活动计划",
        [
            Option(item.plan_id, item.plan_id, truncate(item.title, 40))
            for item in index.plans
        ],
        current=index.active_plan_id,
    )
    if picked is not None:
        _activate(context, picked.key)


def cmd_todo(context: CommandContext, _argument: str) -> None:
    planning = context.runtime.tools.planning.load()
    todo = planning.todo
    if todo is None or not todo.items:
        context.console.print(Text("当前没有待办", style=STYLE_DIM))
        return
    rule(context.console, "待办")
    for index, item in enumerate(todo.items, start=1):
        mark = _STATUS_MARKS.get(item.status, "○")
        style = STYLE_DIM if item.status is TodoStatus.DONE else ""
        context.console.print(Text(f" {mark} {index}. {item.title}", style=style))
    for note in planning.diagnostics:
        context.console.print(Text(f"  {note}", style=STYLE_DIM))


def _activate(context: CommandContext, plan_id: str) -> None:
    if context.runtime.activate_plan(plan_id):
        ok(context.console, f"活动计划: {plan_id}")
    else:
        error(context.console, f"计划不存在: {plan_id}")
