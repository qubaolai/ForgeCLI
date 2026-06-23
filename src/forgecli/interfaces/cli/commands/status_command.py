"""/status 查看当前状态信息。"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.slash_commands import CommandHandler, CommandRegistry
from forgecli.domain.intents import SlashCommand
from forgecli.shared import __version__


class StatusCommand(CommandHandler):
    """06-23 的最小 /status handler。

    当前只证明 slash command 能通过 registry 分派到 handler；真实 session、计划、
    审批队列等状态会在后续 roadmap 日期接入。
    """

    def __init__(self, registry: CommandRegistry, output: UserOutput) -> None:
        self._registry = registry
        self._output = output

    def execute(self, command: SlashCommand) -> None:
        self._show_one(command.command)

    def _show_one(self, name: str) -> None:
        spec = self._registry.get(name)
        if spec is None:
            self._output.print(f"未知命令 /{name}。输入 /help 查看可用命令。")
            return
        self._output.print(f"当前系统{__version__}运行状态 暂未实现")
