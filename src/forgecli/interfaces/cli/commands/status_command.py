"""/status 查看当前状态信息。

26 日切片：展示当前模式与可操作工作区目录列表（ADR-0008 的 ``cwd:`` 语义）。
session id / event id / 读取 state 文件属于 27 日，不在此处。
"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.project import ProjectContext
from forgecli.application.session.session_service import SessionService
from forgecli.application.slash_commands import CommandHandler
from forgecli.domain.intents import SlashCommand


class StatusCommand(CommandHandler):
    """展示当前模式与工作区目录列表。"""

    def __init__(
        self, session: SessionService, context: ProjectContext, output: UserOutput
    ) -> None:
        self._session = session
        self._context = context
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        snapshot = self._session.current()
        lines = [
            f"session: {snapshot.session_id}",
            f"mode: {snapshot.mode.value}",
            f"last_event: {snapshot.last_event_id or '-'}",
            "cwd:",
        ]
        lines.extend(f"- {root}" for root in self._context.project.workspace_roots)
        self._output.print("\n".join(lines))
        return False  # 纯查看，不写任何持久状态
