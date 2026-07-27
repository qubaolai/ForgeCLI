"""POSIX(termios/tty)按键读取适配器。"""

from __future__ import annotations

import os
import select
import termios
from collections.abc import Generator
from contextlib import contextmanager

from forgecli.interfaces.cli.tty.keys import Key, KeyPress


@contextmanager
def raw_mode(fd: int) -> Generator[None]:
    """进入"准 raw"模式：逐字符、无回显、自行处理 Ctrl-C。

    关键：不像 ``tty.setraw`` 那样关闭 OPOST/ONLCR——保留输出后处理，让 "\\n" 仍输出为
    "\\r\\n"。否则换行后光标不回到行首，``rich.Live`` 的就地重绘会错位、把每帧反复堆叠
    在屏幕上(出现层层右移的面板)。
    """
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    # iflag：关掉 CR->NL、流控、奇偶校验等输入翻译。
    new[0] &= ~(
        termios.IGNBRK
        | termios.BRKINT
        | termios.PARMRK
        | termios.ISTRIP
        | termios.INLCR
        | termios.IGNCR
        | termios.ICRNL
        | termios.IXON
    )
    # lflag：关掉规范模式、回显、扩展输入、信号(Ctrl-C 交给 read_key 当普通按键)。
    new[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
    # oflag(new[1]) 故意不动：保留 OPOST/ONLCR，保证 rich.Live 就地重绘正确。
    # VMIN=1 / VTIME=0：阻塞直到读到 1 个字节。
    new[6][termios.VMIN] = 1
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSADRAIN, new)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _utf8_len(first: int) -> int:
    if first < 0x80:
        return 1
    if first >> 5 == 0b110:
        return 2
    if first >> 4 == 0b1110:
        return 3
    if first >> 3 == 0b11110:
        return 4
    return 1


def read_key(fd: int) -> KeyPress:
    raw = os.read(fd, 1)
    if not raw:
        return KeyPress(Key.OTHER)
    b = raw[0]
    if b == 3:  # Ctrl-C
        return KeyPress(Key.CTRL_C)
    if b in (10, 13):
        return KeyPress(Key.ENTER)
    if b in (8, 127):
        return KeyPress(Key.BACKSPACE)
    if b == 9:  # Tab
        return KeyPress(Key.TAB)
    if b == ord("/"):
        return KeyPress(Key.SLASH, "/")
    if b == 27:  # ESC：单独 ESC，或 CSI 方向键 \x1b[A..D
        ready, _, _ = select.select([fd], [], [], 0.0005)
        if not ready or os.read(fd, 1) != b"[":
            return KeyPress(Key.ESC)
        code = os.read(fd, 1)
        return KeyPress(
            {b"A": Key.UP, b"B": Key.DOWN, b"C": Key.RIGHT, b"D": Key.LEFT}.get(
                code, Key.OTHER
            )
        )
    char = (raw + os.read(fd, _utf8_len(b) - 1)).decode("utf-8", errors="ignore")
    if char and char.isprintable():
        return KeyPress(Key.CHAR, char)
    return KeyPress(Key.OTHER)
