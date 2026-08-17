"""Windows 交互式 Shell 交接 (ADR-0017 §8.2).

与 POSIX 那边同样的思路: **不接管字节流**. Windows 控制台子进程默认继承父进程的控制台
句柄, 而 Forge 的控制台就是用户的控制台 —— 直接把它交给 PowerShell/cmd, 用户拿到的就是
原生控制台, 颜色, 补全, Ctrl-C, 窗口尺寸与 Unicode 全部由控制台自己处理.

ConPTY 是"把控制台**嵌进**另一个程序的窗口"时才需要的东西. 这里没有第二个窗口, 用户
看的就是这个控制台, 引入 ConPTY 只会多出一层需要手工同步尺寸与转发字节的中间层, 而
§9 明确禁止观察这些字节.

进程组那一步在 Windows 上没有对应物: Ctrl-C 的路由由控制台自己按前台进程决定.
但**必须**在人工 Shell 期间摘掉 Forge 自己的 Ctrl-C 处理, 否则 Forge 会把用户中断
命令的那次 Ctrl-C 当成对自己的.

未在 Windows 上实测过. 若行为与本文档不符, 按 §8.2 应当**明确拒绝并给出诊断**,
而不是降级成伪交互管道.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from forgecli.application.manual_shell.provider import (
    InteractiveShellProvider,
    ManualShellUnavailable,
)
from forgecli.domain.manual_shell.request import ManualShellRequest
from forgecli.domain.manual_shell.result import ManualShellResult

__all__ = ["WindowsInteractiveShellProvider"]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class WindowsInteractiveShellProvider(InteractiveShellProvider):
    def open(self, request: ManualShellRequest) -> ManualShellResult:
        _require_console()
        started = _now()
        try:
            process = subprocess.Popen(  # noqa: S603 - argv 由 resolver 构造
                [request.shell_executable, *request.argv[1:]],
                cwd=request.cwd,
                env=dict(request.environment),
                stdin=None,
                stdout=None,
                stderr=None,
                close_fds=False,  # Windows 上控制台句柄要继承
            )
        except (OSError, ValueError) as exc:
            return ManualShellResult(
                started_at=started, finished_at=_now(), start_error=str(exc)
            )

        with _detached_interrupt():
            code = _wait(process)
        return ManualShellResult(started_at=started, finished_at=_now(), exit_code=code)


def _require_console() -> None:
    """没有真控制台就明确拒绝 (§8.2), 不降级成管道子进程."""
    for stream in (sys.stdin, sys.stdout):
        try:
            if os.isatty(stream.fileno()):
                return
        except (OSError, ValueError, AttributeError):
            continue
    raise ManualShellUnavailable(
        "当前没有可交互的 Windows 控制台, 无法进入 Shell 模式. "
        "请在 Windows Terminal, PowerShell 或 cmd 窗口中运行 forge."
    )


@contextmanager
def _detached_interrupt() -> Iterator[None]:
    """人工 Shell 期间把 Ctrl-C 让给子进程.

    Forge 平时把 SIGINT 接成"取消当前 turn". Shell 活跃时那个语义是错的 —— 用户按
    Ctrl-C 是想中断自己刚跑的命令 (§7).
    """
    try:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (OSError, ValueError):
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _wait(process: subprocess.Popen[bytes]) -> int:
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            continue
