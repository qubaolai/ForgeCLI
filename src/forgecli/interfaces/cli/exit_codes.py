"""进程退出码的封闭定义: 一个退出码只对应一个原因.

契约: **0 表示 Forge 本地服务正常停止；任何非 0 都表示服务没能开始**，码值说明
是哪一道前提没满足。脚本与 CI 可以据此分流，不必去 grep 提示文案。

    0  本地 Web 服务正常停止
    6  项目已被另一个 forge 进程占用

3–5 是旧终端入口的兼容保留码，新 Web 启动路径不再产生。

1 与 2 有意留空:
    - 1 是"未预期错误"的通用约定, 留给未捕获异常 (Python 自身即以 1 退出), 这样
      "已知的拒绝" 与 "程序炸了" 在脚本里能分开.
    - 2 归 Click / Typer 的用法错误 (参数写错), 不与产品语义冲突.

新增退出码 = 在这里加一条, 并在上面的表里写清原因; 不要在别处散落裸数字.
"""

from __future__ import annotations

from enum import IntEnum

__all__ = ["ExitCode"]


class ExitCode(IntEnum):
    """forge 进程退出码. IntEnum: 可直接当 int 返回给 Typer."""

    OK = 0
    ELEVATED = 3
    NO_TTY = 4
    UNTRUSTED = 5
    PROJECT_LOCKED = 6
