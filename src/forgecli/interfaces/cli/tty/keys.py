"""这个进程能不能做终端交互.

按键模型不在这里: 它是 prompt-toolkit 的 `Keys` 与 `KeyPress` (ADR-0040 决策 4.3).
留下这一个函数是因为它不是按键机制, 而是"要不要进交互路径"这个产品判断, 而且它的调用方
(bootstrap / repl) 在决定之前根本不该去 import 整套输入实现.
"""

from __future__ import annotations

import sys


def stdin_is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
