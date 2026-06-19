"""/help: 列出可用命令, 或查看某个命令的帮助(支持 /help config)。"""

from __future__ import annotations

from forgecli.application.commands.base import CommandHandler
from forgecli.application.commands.registry import CommandRegistry
from forgecli.application.ports import Output
from forgecli.domain.intents import SlashCommand


class HelpCommand(CommandHandler):
    def __init__(self, registry: CommandRegistry, output: Output) -> None:
        self._registry = registry
        self._output = output

    def execute(self, command: SlashCommand) -> None:
        if command.args:
            self._show_one(command.args[0].lstrip("/").lower())
        else:
            self._show_all()

    def _show_all(self) -> None:
        self._output.print("可用命令：")
        for spec in self._registry.all_specs():
            self._output.print(f"  /{spec.name:<8} {spec.summary}")

    def _show_one(self, name: str) -> None:
        spec = self._registry.get(name)
        if spec is None:
            self._output.print(f"未知命令 /{name}。输入 /help 查看可用命令。")
            return
        self._output.print(f"/{spec.name} — {spec.summary}")