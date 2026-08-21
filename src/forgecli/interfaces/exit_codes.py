"""进程退出码的封闭定义: 一个退出码只对应一个原因.

契约: **0 表示 Forge 正常停止；任何非 0 都表示这一次没能开始**，码值说明是哪一道
前提没满足。脚本与 CI 可以据此分流，不必去 grep 提示文案。

    0  正常停止（Web 服务停止，或终端会话退出）
    3  以特权身份运行           仅 forge cli
    4  没有真终端                仅 forge cli
    5  用户未信任当前目录        仅 forge cli
    6  项目已被另一个 forge 进程占用
    7  端口被占用                仅裸 forge

**6 两条路径都会产生**：CLI 与 Web 取的是同一把项目级 ``forge.lock``，所以"另一个
Forge 正在跑"这件事与对方是哪种入口无关，退出码也就不该分两个。

3–5 只可能来自 ``forge cli``：裸 ``forge`` 起的是服务，不需要 TTY，也在没有信任关系
时照常提供项目中心页面。

1 与 2 有意留空:
    - 1 是"未预期错误"的通用约定, 留给未捕获异常 (Python 自身即以 1 退出), 这样
      "已知的拒绝" 与 "程序炸了" 在脚本里能分开.
    - 2 归 Click / Typer 的用法错误 (参数写错), 不与产品语义冲突.

新增退出码 = 在这里加一条, 并在上面的表里写清原因; 不要在别处散落裸数字.

**它住在 interfaces/ 而不是 interfaces/cli/**: 退出码是进程级契约, 裸 forge 与
forge cli 两条启动路径共用. 早先它在 cli 包下, 于是 web 那一侧只能自己写 6 与 7
两个裸数字 —— 同一个契约存两份, 而漂移的那一份不会报错.
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
    PORT_BUSY = 7
