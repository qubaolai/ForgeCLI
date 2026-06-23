"""按平台选择按键读取实现，并重新导出平台无关的按键模型。

menu_presenter / repl 只 import 本模块，无需关心底层是 termios 还是 msvcrt。
"""

from __future__ import annotations

import sys

from forgecli.interfaces.cli.tty.keys import Key, KeyPress, stdin_is_tty

if sys.platform == "win32":
    from forgecli.interfaces.cli._tty_windows import raw_mode, read_key
else:
    from forgecli.interfaces.cli.tty._tty_posix import raw_mode, read_key

__all__ = ["Key", "KeyPress", "raw_mode", "read_key", "stdin_is_tty"]
