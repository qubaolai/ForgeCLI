"""/plan 的子命令与 /todo: 查看与切换当前计划和待办 (ADR-0022 §5.3).

注意 `/plan` **裸命令的既有语义是切到 plan 档**, 保持不变 —— 这里只接子命令. 把裸
`/plan` 改成"显示计划"会让一个用了很久的档位切换手势突然变成别的东西.

只读与切指针, 不做裁决: 批准一份计划要走 ADR-0023 的评审, 不能从这里绕过去.
"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.planning.planning_service import PlanningService
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.domain.intents import SlashCommand

__all__ = ["PlanCommand", "TodoCommand"]

_USAGE = "用法: /plan show | /plan list | /plan use <plan_id>"


class PlanCommand(CommandHandler):
    """`/plan show|list|use` 的处理. 裸 `/plan` 不到这里, 它仍然是切档."""

    def __init__(self, planning: PlanningService, output: UserOutput) -> None:
        self._planning = planning
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        action = command.args[0] if command.args else ""
        if action == "show":
            return self._show()
        if action == "list":
            return self._list()
        if action == "use":
            return self._use(command.args[1] if len(command.args) > 1 else "")
        self._output.print(_USAGE)
        return True

    def _show(self) -> bool:
        body = self._planning.read_plan()
        if body is None:
            self._output.print("当前没有生效的计划.")
            return True
        for line in body.splitlines():
            self._output.print(line)
        return True

    def _list(self) -> bool:
        index = self._planning.load_index()
        if not index.plans:
            self._output.print("本项目还没有计划.")
            return True
        for summary in index.plans:
            marker = "*" if summary.plan_id == index.active_plan_id else " "
            self._output.print(
                f"{marker} {summary.plan_id}  r{summary.revision}  "
                f"{summary.status.value:<11}{summary.title}"
            )
        self._output.print("\n带 * 的是当前活动计划.")
        return True

    def _use(self, plan_id: str) -> bool:
        if not plan_id:
            self._output.print(_USAGE)
            return True
        if not self._planning.set_active_plan(plan_id):
            self._output.print(f"没有这份计划: {plan_id}")
            return True
        self._output.print(f"活动计划已切到 {plan_id}.")
        return True


class TodoCommand(CommandHandler):
    def __init__(self, planning: PlanningService, output: UserOutput) -> None:
        self._planning = planning
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        todo = self._planning.read_todo()
        if todo is None or not todo.items:
            self._output.print("当前没有待办清单.")
            return True
        for line in todo.render().splitlines():
            self._output.print(line)
        self._output.print(f"\n进度 {todo.done_count}/{todo.total_count}.")
        return True
