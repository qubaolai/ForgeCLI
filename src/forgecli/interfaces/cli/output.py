"""Output 端口的 Rich 实现：命令处理器经由它回显，不直接依赖 Rich Console。"""

from __future__ import annotations

from rich.console import Console

from forgecli.application.interaction_ports import UserOutput


class RichOutput(UserOutput):
    def __init__(self, console: Console) -> None:
        self._console = console

    def print(self, message: str) -> None:
        self._console.print(message)
