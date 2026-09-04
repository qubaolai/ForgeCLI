"""退出交互式会话的信号.

单独一个模块, 因为它有两个抛出点 —— 输入框 (空行连按两次 Ctrl-C) 和 `/exit` 命令 ——
而这两处互相不该有依赖. 放在 `prompt.py` 里会让 `/exit` 为了拿一个异常类去 import
整套 prompt-toolkit 输入实现.

用异常而不是返回特殊值来表达退出: 退出可能发生在读取输入时, 也可能发生在分派命令的
深处, 靠返回值往上传要让沿途每一层都记得转发一次, 漏一层就退不掉.
"""

from __future__ import annotations

__all__ = ["SessionExit"]


class SessionExit(Exception):
    """用户要求结束当前交互式会话. REPL 主循环捕获它并正常收尾."""
