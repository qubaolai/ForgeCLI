"""/exit: 安全退出当前交互式会话 (ADR-0007).

"安全"在这里是具体的, 不是修饰词: 会话事件与快照在每次动作发生时就已经落盘 (见
SessionService._append), 所以退出不需要做一次收尾刷盘 —— 需要收尾刷盘的设计意味着
中途崩溃就会丢事件, 那是另一个要修的问题, 不该由退出命令来兜.

因此这里只做一件事: 抛 SessionExit 把控制权交回 REPL 主循环. 不 sys.exit, 不动状态,
也不打断在途的 turn —— 命令是在 turn 之间被分派的, 分派到这里时本来就没有在跑的工具.
"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.domain.intents import SlashCommand
from forgecli.interfaces.cli.session_exit import SessionExit

__all__ = ["ExitCommand"]


class ExitCommand(CommandHandler):
    def __init__(self, output: UserOutput) -> None:
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        self._output.print("退出会话. 下次可用 /resume 接回本次对话.")
        raise SessionExit
