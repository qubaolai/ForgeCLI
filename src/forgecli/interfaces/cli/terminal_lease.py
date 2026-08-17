"""TerminalLease: Forge 内交接终端的唯一协调点 (ADR-0017 §7).

顺序不是随便排的, 每一步都在防一件具体的事:

    收掉 Rich Live        Live 与子 Shell 抢同一块屏幕, 不收会互相覆盖
      -> 保存 termios     子 Shell (以及它跑的 vim) 一定会改终端属性
      -> 交出终端
      -> 恢复 termios     恢复的是**进入前**的状态, 不是"默认状态"
      -> 重绘边界

恢复必须走 ``finally``, 覆盖正常退出, 启动失败, Shell 崩溃与信号退出 (§7). 这里做不到
的只有 Forge 自己被 SIGKILL —— 那种情况谁都做不到.

为什么不用 ``termios.tcsetattr(fd, TCSADRAIN, ...)`` 之外的东西: 保存与恢复要对称,
一方用 TCSANOW 另一方用 TCSADRAIN 会在有未读输入时表现不同.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console

from forgecli.application.manual_shell.provider import TerminalLease
from forgecli.interfaces.cli.run_renderer import TerminalRunRenderer

__all__ = ["CliTerminalLease"]


class CliTerminalLease(TerminalLease):
    """把终端从 Forge 手里租给别人, 用完还回来.

    renderer 可选: 人工 Shell 只在 turn 之间进入, 那时 Live 本来就没开. 传进来是为了
    覆盖"以后 REPL 支持后台 turn"的情况 —— 到那时这里已经是对的, 不需要再想起来改.
    """

    def __init__(
        self, console: Console, renderer: TerminalRunRenderer | None = None
    ) -> None:
        self._console = console
        self._renderer = renderer

    @contextmanager
    def acquire(self) -> Iterator[None]:
        """租约只管终端**状态**, 不打任何字.

        分隔用的空行原来在这里, 已经挪走: `# clear` 之后再打一行, 刚清干净的屏幕就又
        有东西了 —— 而用户要的正是那块干净屏幕. 打不打字是界面的决定, 归 ShellModeEntry.
        """
        if self._renderer is not None:
            # 把活动区里剩的半行定稿并清空, 再让出屏幕.
            self._renderer.finish()
        saved = _save_terminal()
        try:
            yield
        finally:
            _restore_terminal(saved)


def _save_terminal() -> tuple[int, Any] | None:
    """快照 stdin 的终端属性. 拿不到就返回 None, 恢复阶段跳过.

    不抛错: 没有终端属性可保存 (被重定向, 或在 pytest 捕获下) 不该让人工 Shell 起不来 ——
    真正需要终端的是 Provider, 它自己会拒绝.
    """
    try:
        import termios

        fd = sys.stdin.fileno()
        return fd, termios.tcgetattr(fd)
    except Exception:
        return None


def _restore_terminal(saved: tuple[int, Any] | None) -> None:
    """恢复到**进入前**的属性.

    这一步在 vim/top 之类改了 raw mode 又没清理干净时是唯一的补救 —— 用户回到 Forge
    发现键盘没回显, 只能重开终端.
    """
    if saved is None:
        return
    fd, attributes = saved
    try:
        import termios

        termios.tcsetattr(fd, termios.TCSADRAIN, attributes)
    except Exception:
        # 恢复失败是终端基础设施问题, 但这里不能抛: 抛出去会盖掉真正的 Shell 结果.
        # 用户能看到的现象是终端行为异常, 而 ADR §12 要求那时安全退出而不是继续接指令.
        pass
