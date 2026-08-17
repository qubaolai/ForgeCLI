"""POSIX 交互式 Shell 交接 (ADR-0017 §8.1).

## 为什么不开 PTY

ADR 写的是"使用 PTY **或等价的前台进程组交接**". 这里选后者, 理由是前者在这个场景下
反而更弱:

开 PTY 意味着 Forge 坐在用户和 Shell 中间, 每个字节都要经 Forge 转发一次. 那是"需要
观察或改写流"时的做法 —— 而 ADR §9 恰恰**禁止**观察: 不捕获 stdin, stdout, stderr,
不记录 history. 既然一个字节都不许看, 转发就只剩下代价: 多一层缓冲会破坏全屏程序的
时序, SIGWINCH 要手工同步, 信号要手工翻译, 而且 Forge 进程里凭空多出一份用户输入.

Forge 的 stdin/stdout **本来就是**用户的真实终端 (bootstrap 已经拒绝了非 TTY 启动).
把这个终端直接交给子进程, 用户拿到的就是原生终端, 没有中间层:

    子进程独立进程组 (process_group=0)
      -> tcsetpgrp 把它设为前台
      -> 它拥有 controlling terminal, 于是 job control, 补全, 全屏程序全部原生可用
      -> 等它结束
      -> tcsetpgrp 交还 Forge

## 前台进程组这一步不能省

只 Popen 不 tcsetpgrp 的话, 子 Shell 与 Forge 在同一个进程组里, Ctrl-C 会同时打到两边 ——
用户想中断的是自己那条命令, 结果 Forge 也收到了 SIGINT. ADR §7 明确要求"Forge 不把
Shell 内 Ctrl-C 解释为 Agent cancel".

tcsetpgrp 自己有个坑: 从**非前台**进程组调用它会收到 SIGTTOU 而被停住. 所以两次调用
期间要临时忽略 SIGTTOU, 结束后原样恢复.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime

from forgecli.application.manual_shell.provider import (
    InteractiveShellProvider,
    ManualShellUnavailable,
)
from forgecli.domain.manual_shell.request import ManualShellRequest
from forgecli.domain.manual_shell.result import ManualShellResult

__all__ = ["PosixInteractiveShellProvider"]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class PosixInteractiveShellProvider(InteractiveShellProvider):
    def open(self, request: ManualShellRequest) -> ManualShellResult:
        fd = _controlling_tty_fd()
        started = _now()
        try:
            process = subprocess.Popen(  # noqa: S603 - argv 由 resolver 构造, 非用户输入
                [request.shell_executable, *request.argv[1:]],
                cwd=request.cwd,
                env=dict(request.environment),
                # 独立进程组: 前提条件, 否则 tcsetpgrp 无从谈起, Ctrl-C 也会打到 Forge.
                process_group=0,
                # 三个标准流原样继承 —— 不接管, 不缓冲, 不观察.
                stdin=None,
                stdout=None,
                stderr=None,
                close_fds=True,
            )
        except (OSError, ValueError) as exc:
            return ManualShellResult(
                started_at=started, finished_at=_now(), start_error=str(exc)
            )

        with _foreground(fd, process.pid):
            # wait 不能用 timeout: 人工 Shell 想开多久开多久, 这里等的是用户.
            status = _wait(process)
        return ManualShellResult(
            started_at=started,
            finished_at=_now(),
            exit_code=status[0],
            signal=status[1],
        )


def _controlling_tty_fd() -> int:
    """拿到控制终端的 fd.

    三个标准流都不是 TTY 时直接拒绝, 不降级 (§8.2): 一个跑在管道里的"交互式 Shell"
    会立刻退出或读到 EOF, 而用户看到的是 Forge 莫名其妙闪了一下.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fd = stream.fileno()
        except (OSError, ValueError, AttributeError):
            continue
        if os.isatty(fd):
            return fd
    raise ManualShellUnavailable(
        "当前不在终端 (TTY) 中, 无法把终端交给交互式 Shell. "
        "请在真实终端里运行 forge, 不要经管道或重定向."
    )


@contextmanager
def _foreground(fd: int, pgid: int) -> Iterator[None]:
    """把 pgid 设为前台进程组, 退出时交还 Forge.

    整段是 best effort: 拿不到前台控制权 (例如 Forge 自己就在后台) 时不该让人工 Shell
    起不来 —— 它仍然能跑, 只是 Ctrl-C 的归属没那么干净. 但终端交还必须走 finally.
    """
    try:
        original = os.tcgetpgrp(fd)
    except OSError:
        yield
        return
    # tcsetpgrp 从非前台进程组调用会招来 SIGTTOU 把自己停住.
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        _set_foreground(fd, pgid)
        yield
    finally:
        _set_foreground(fd, original)
        signal.signal(signal.SIGTTOU, previous)


def _set_foreground(fd: int, pgid: int) -> None:
    # 子进程可能已经退出, 或者 Forge 本来就不在前台. 两种都不值得中断流程.
    with suppress(OSError):
        os.tcsetpgrp(fd, pgid)


def _wait(process: subprocess.Popen[bytes]) -> tuple[int | None, int | None]:
    """等子 Shell 结束, 区分正常退出与被信号杀掉.

    Popen.returncode 用负数表示信号 —— 那是一个编码技巧, 不该泄漏到界面上:
    "exit -9" 对用户没有意义, "被信号 9 终止"才有.
    """
    while True:
        try:
            code = process.wait()
            break
        except KeyboardInterrupt:
            # Ctrl-C 归子 Shell. Forge 这边收到只是因为信号投递的边缘情况,
            # 不能把它当成"用户要退出 Forge" (§7).
            continue
    return (None, -code) if code < 0 else (code, None)
