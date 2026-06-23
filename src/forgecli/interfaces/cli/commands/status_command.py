"""/status 查看当前状态信息。

26 日切片：展示当前模式与可操作工作区目录列表（ADR-0008 的 ``cwd:`` 语义）。
session id / event id / 读取 state 文件属于 27 日，不在此处。
"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.project import ProjectContext
from forgecli.application.session import SessionState
from forgecli.application.slash_commands import CommandHandler
from forgecli.domain.intents import SlashCommand


class StatusCommand(CommandHandler):
    """展示当前模式与工作区目录列表。"""

    def __init__(
        self, state: SessionState, context: ProjectContext, output: UserOutput
    ) -> None:
        self._state = state
        self._context = context
        self._output = output

    def execute(self, command: SlashCommand) -> None:
        lines = [f"mode: {self._state.mode.value}", "cwd:"]
        lines.extend(f"- {root}" for root in self._context.project.workspace_roots)
        self._output.print("\n".join(lines))
