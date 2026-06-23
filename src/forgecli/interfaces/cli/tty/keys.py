"""平台无关的按键模型与 TTY 判断。"""

from __future__ import annotations

import enum
import sys
from dataclasses import dataclass
from enum import Enum


class Key(Enum):
    UP = enum.auto()
    DOWN = enum.auto()
    LEFT = enum.auto()
    RIGHT = enum.auto()
    ENTER = enum.auto()
    ESC = enum.auto()
    BACKSPACE = enum.auto()
    SLASH = enum.auto()
    CTRL_C = enum.auto()
    CHAR = enum.auto()  # 可打印字符，见 .char
    OTHER = enum.auto()


@dataclass(frozen=True)
class KeyPress:
    key: Key
    char: str = ""


def stdin_is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
