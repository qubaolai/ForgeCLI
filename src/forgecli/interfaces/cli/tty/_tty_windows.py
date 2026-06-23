"""Windows 控制台按键读取(msvcrt)。

msvcrt.getwch() 逐字符读取、无回显，无需像 POSIX 那样切换终端模式，因此
raw_mode 是空操作。方向键 / 功能键以两字节序列到达：先一个前缀字符
('\\x00' 普通功能键 / '\\xe0' 方向与编辑键)，再一个扫描码。
"""

from __future__ import annotations

import msvcrt  # 仅 Windows 存在
from collections.abc import Iterator
from contextlib import contextmanager

from forgecli.interfaces.cli.tty.keys import Key, KeyPress

# '\x00' / '\xe0' 之后的扫描码 -> 方向键
_ARROWS = {"H": Key.UP, "P": Key.DOWN, "K": Key.LEFT, "M": Key.RIGHT}


@contextmanager
def raw_mode(fd: int) -> Iterator[None]:
    # getwch 直接读控制台，无需修改终端模式；fd 不使用。
    yield


def read_key(fd: int) -> KeyPress:
    ch = msvcrt.getwch()  # type: ignore
    if ch == "\x03":  # Ctrl-C（getwch 会返回 \x03，不抛 KeyboardInterrupt）
        return KeyPress(Key.CTRL_C)
    if ch in ("\r", "\n"):
        return KeyPress(Key.ENTER)
    if ch == "\x08":  # Backspace
        return KeyPress(Key.BACKSPACE)
    if ch == "/":
        return KeyPress(Key.SLASH, "/")
    if ch == "\x1b":  # ESC（Windows 方向键走下面的前缀分支，不经 ESC）
        return KeyPress(Key.ESC)
    if ch in ("\x00", "\xe0"):  # 功能键 / 方向键前缀
        return KeyPress(_ARROWS.get(msvcrt.getwch(), Key.OTHER))  # type: ignore
    if ch.isprintable():
        return KeyPress(Key.CHAR, ch)
    return KeyPress(Key.OTHER)
