"""/resume 查看 / 恢复历史会话。

形态：
    - ``/resume``            列出可恢复会话（按标题），最近在前。
    - ``/resume <关键字>``   按 title / id 子串搜索并列出（当参数不是已知会话 id 时）。
    - ``/resume <session_id>`` 真正恢复该会话：重指向活动会话并续写到同一 events.jsonl。
"""

from __future__ import annotations

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.menu import Choice, Menu
from forgecli.application.project import ProjectContext
from forgecli.application.session import SessionService
from forgecli.application.session.resume_service import ResumeService
from forgecli.application.slash_commands import CommandHandler
from forgecli.domain.intents import SlashCommand
from forgecli.domain.session.snapshot import SessionSnapshot

_NO_TITLE = "（无标题）"


class ResumeCommand(CommandHandler):
    """列出 / 搜索 / 恢复历史会话。"""

    def __init__(
        self,
        service: ResumeService,
        session: SessionService,
        agent_turn: AgentTurnService,
        context: ProjectContext,
        presenter: MenuPresenter,
        output: UserOutput,
    ) -> None:
        self._service = service
        self._session = session
        self._agent_turn = agent_turn
        self._context = context
        self._presenter = presenter
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        if not command.args:
            self._present(self._service.list_sessions(), query=None)
            return False

        target = command.args[0]
        if self._service.has_session(target):
            self._resume(target)
        else:
            self._present(self._service.list_sessions(query=target), query=target)
        return False

    def _resume(self, session_id: str) -> None:
        snapshot, history = self._service.load_full(session_id)
        current_root = self._context.project.primary_workspace_root
        if snapshot.workspace_root != current_root:
            self._output.print(
                f"跨目录(项目)恢复暂未实现：该会话属于 {snapshot.workspace_root}，"
                f"当前为 {current_root}。"
            )
            return
        self._session.resume(snapshot, history)
        self._agent_turn.resume(history)
        self._output.print(_summary(snapshot, len(history)))

    def _present(self, sessions: list[SessionSnapshot], *, query: str | None) -> None:
        if not sessions:
            self._output.print(
                f"未找到匹配「{query}」的会话。" if query else "暂无可恢复会话。"
            )
            return
        chosen: list[str] = []
        choices = tuple(self._choice(snapshot, chosen) for snapshot in sessions)
        title = f"恢复历史会话 · 匹配「{query}」" if query else "恢复历史会话"
        self._presenter.present(Menu(title=title, choices=choices))
        if chosen:
            # 用户在菜单里 Enter 选中了某个会话
            self._resume(chosen[-1])

    def _choice(self, snapshot: SessionSnapshot, chosen: list[str]) -> Choice:
        session_id = snapshot.session_id
        return Choice(
            label=snapshot.title or _NO_TITLE,
            preview=lambda: f"{snapshot.updated_at}",
            payload=lambda: _preview_block(snapshot),
            on_select=lambda: chosen.append(session_id),
            close_on_select=True,
        )


def _preview_block(snapshot: SessionSnapshot) -> str:
    """Space 预览：会话摘要（只读快照字段，不加载事件）。"""
    return "\n".join(
        [
            f"title: {snapshot.title or _NO_TITLE}",
            f"session: {snapshot.session_id}",
            f"status: {snapshot.status}",
            f"updated_at: {snapshot.updated_at}",
            f"last_event: {snapshot.last_event_id or '-'}",
        ]
    )


def _summary(snapshot: SessionSnapshot, event_count: int) -> str:
    return "\n".join(
        [
            "已恢复会话：",
            f"  title: {snapshot.title or _NO_TITLE}",
            f"  session: {snapshot.session_id}",
            f"  status: {snapshot.status}",
            f"  updated_at: {snapshot.updated_at}",
            f"  last_event: {snapshot.last_event_id or '-'}",
            f"  共 {event_count} 条事件",
            "继续输入即可续写（不回放历史对话）。",
        ]
    )
