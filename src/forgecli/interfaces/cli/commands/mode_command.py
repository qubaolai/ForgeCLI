"""/chat、/plan、/act 模式切换命令。

模式切换归一为普通斜杠命令：handler 调 ``SessionService.record_mode_change`` 把
mode 单一真相推进到 session 快照（并写一条 ``mode_changed`` 事件）。因为它已经写了
自己的语义事件，``execute`` 返回 ``False``，避免 REPL 再叠一条通用 ``slash_command``
事件——"统一路由路径"不等于"压平事件语义"。
"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.session import SessionService
from forgecli.application.slash_commands import CommandHandler
from forgecli.domain.intents import SessionMode, SlashCommand


class ModeCommand(CommandHandler):
    """把当前会话切换到构造时绑定的某个模式。"""

    def __init__(
        self, mode: SessionMode, session: SessionService, output: UserOutput
    ) -> None:
        self._mode = mode
        self._session = session
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        self._session.record_mode_change(self._mode)
        self._output.print(f"已切换到 [bold]{self._mode.value}[/] 模式。")
        return False  # 已写 mode_changed 事件，无需 REPL 再记 slash_command
