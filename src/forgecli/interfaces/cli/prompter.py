"""Prompter / Output 的 Rich 实现。一次性选择/确认/输入用方向键, ESC 取消"""

from __future__ import annotations
import sys
from typing import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from forgecli.application.ports import Output, Prompter
from forgecli.interfaces.cli.tty import Key, raw_mode, read_key, stdin_is_tty

class RichOutput(Output):
    def __init__(self, console: Console) -> None:
        self._console = console

    def print(self, message: str) -> None:
        self._console.print(message)

