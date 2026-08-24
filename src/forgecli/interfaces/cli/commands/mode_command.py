"""模式相关命令：/mode 查看与选择，/plan /accept-edits /auto /full-access 直接切换。

模式切换归一为普通斜杠命令：handler 调 ``SessionService.set_mode`` 把 mode 单一真相
推进到 session 快照（并写一条 ``mode_changed`` 事件）。因为它已经写了自己的语义事件，
``execute`` 返回 ``False``，避免 REPL 再叠一条通用 ``slash_command`` 事件——"统一路由
路径"不等于"压平事件语义"。

四条直达命令与 /mode 面板并存不是冗余：直达命令给知道自己要去哪一档的人，面板给
"不确定现在在哪档、有哪几档"的人。两者走同一个 ``set_mode``，事件日志里不可区分。
"""

from __future__ import annotations

from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.menu import Choice, Menu
from forgecli.application.session.session_service import SessionService
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.domain.intents import SessionMode, SlashCommand

__all__ = ["ModeCommand", "ModeSelectCommand"]

# 每档权限梯度的一句话说明。按 _MODE_LADDER 的从紧到松排列（ADR-0009 决策 6）。
_MODE_SUMMARY: dict[SessionMode, str] = {
    SessionMode.PLAN: "只读：只看不改，写工具不进模型目录",
    SessionMode.ACCEPT_EDITS: "放行低风险编辑，命令仍逐次询问",
    SessionMode.AUTO: "额外放行低风险命令，其余仍询问",
    SessionMode.FULL_ACCESS: "可跨工作区与联网，只剩红线兜底",
}

_LADDER: tuple[SessionMode, ...] = (
    SessionMode.PLAN,
    SessionMode.ACCEPT_EDITS,
    SessionMode.AUTO,
    SessionMode.FULL_ACCESS,
)


class ModeCommand(CommandHandler):
    """把当前会话切换到构造时绑定的某个模式。"""

    def __init__(
        self, mode: SessionMode, session: SessionService, output: UserOutput
    ) -> None:
        self._mode = mode
        self._session = session
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        self._session.set_mode(self._mode)
        self._output.print(f"已切换到 [bold]{self._mode.value}[/] 模式。")
        return False  # 已写 mode_changed 事件，无需 REPL 再记 slash_command


class ModeSelectCommand(CommandHandler):
    """/mode：显示当前模式，并打开一档一行的选择面板。

    带参数时直接切换（``/mode plan``），连字符与下划线都收：命令名用连字符
    （/accept-edits），而落盘取值用下划线（accept_edits），用户没有义务记住这个区别。
    """

    def __init__(
        self,
        session: SessionService,
        presenter: MenuPresenter,
        output: UserOutput,
    ) -> None:
        self._session = session
        self._presenter = presenter
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        current = self._session.current().mode
        if command.args:
            return self._switch(command.args[0], current)
        self._output.print(
            f"当前模式 [bold]{current.value}[/] — {_MODE_SUMMARY[current]}"
        )
        self._presenter.present(self._menu())
        return False  # set_mode 自己写 mode_changed

    def _switch(self, raw: str, current: SessionMode) -> bool:
        wanted = raw.strip().lower().replace("-", "_")
        target = next((mode for mode in _LADDER if mode.value == wanted), None)
        if target is None:
            allowed = " / ".join(mode.value for mode in _LADDER)
            self._output.print(f"未知模式 {raw!r}。可选：{allowed}")
            return False
        if target is current:
            self._output.print(f"已经在 [bold]{target.value}[/] 模式。")
            return False
        self._session.set_mode(target)
        self._output.print(f"已切换到 [bold]{target.value}[/] 模式。")
        return False

    def _menu(self) -> Menu:
        return Menu(
            title="选择会话模式（权限从紧到松）",
            choices=tuple(self._choice(mode) for mode in _LADDER),
        )

    def _choice(self, mode: SessionMode) -> Choice:
        def _select() -> None:
            if mode is not self._session.current().mode:
                self._session.set_mode(mode)

        return Choice(
            label=mode.value,
            # preview 每次渲染重读：切换之后当前档标记跟着动，不用重建菜单。
            preview=lambda: (
                f"● {_MODE_SUMMARY[mode]}"
                if mode is self._session.current().mode
                else _MODE_SUMMARY[mode]
            ),
            on_select=_select,
            close_on_select=True,
        )
