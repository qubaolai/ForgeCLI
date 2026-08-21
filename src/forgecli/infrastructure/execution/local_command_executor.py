"""本机子进程执行器: 当前唯一的 CommandExecutor 实现.

四条与安全直接相关的实现细节:

1. **不做 PATH 查找**: argv[0] 必须已经是绝对路径, 由调用方在裁决阶段解析并绑定. 这里
   再查一次 PATH, 就等于允许"裁决的是 /usr/bin/python3, 执行的是工作区里那个 python3".
2. **不继承 os.environ**: 环境是调用方给的已净化快照.
3. **独立进程组**: 超时或取消时整组一起杀, 否则 `bash -c 'sleep 100 &'` 留下的孙进程
   会在 Forge 退出后继续跑.
4. **输出有上限**: 超限即截断并如实标记, 不把几百 MB 的日志读进内存.

第 4 条要靠**边读边丢**才成立. 早先用的是 `communicate()` 再 `_clamp()`: 那是先把完整
输出读进内存, 进程退出之后才截断 —— 上限只影响交给上层的字符串大小, 拦不住内存耗尽,
一条 `yes` 就能在超时之前把 Forge 撑爆. 现在两路输出各由一个后台线程增量读取, 到上限就
停止累积, 但**继续把管道读空**: 不读空, 子进程会因为管道写满而卡住, 连超时都等不到.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time

from forgecli.application.tools.command_executor import (
    CommandExecutor,
    CommandOutcome,
    CommandRequest,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["LocalCommandExecutor"]

# 取消检查的轮询间隔: 协作式取消要在这个粒度上响应.
_POLL_SECONDS = 0.05
# 收到取消或超时后, 留给进程组自行退出的宽限时间, 之后 SIGKILL.
_GRACE_SECONDS = 2.0
# 单次读取的块大小.
_READ_CHUNK_BYTES = 64 * 1024


class _OutputBudget:
    """stdout/stderr 共用一个字节预算，避免两路各拿一份导致上限翻倍。"""

    def __init__(self, limit: int) -> None:
        self._remaining = max(0, limit)
        self._lock = threading.Lock()
        self.overflowed = False

    def take(self, chunk: bytes) -> bytes:
        with self._lock:
            if self._remaining <= 0:
                self.overflowed = self.overflowed or bool(chunk)
                return b""
            kept = chunk[: self._remaining]
            self._remaining -= len(kept)
            if len(kept) < len(chunk):
                self.overflowed = True
            return kept


class _BoundedReader(threading.Thread):
    """读一路输出, 累积到上限就不再收集, 但继续把管道读空.

    "继续读空"是这个类的要点: 停止读取会让子进程卡在写管道上, 于是它既不退出也不再
    产生输出, 超时之外没有任何东西能结束这次调用.
    """

    def __init__(self, stream: object, budget: _OutputBudget) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._budget = budget
        self._chunks: list[bytes] = []

    def run(self) -> None:
        read = getattr(self._stream, "read", None)
        if read is None:
            return
        with contextlib.suppress(OSError, ValueError):
            while True:
                chunk = read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                kept = self._budget.take(chunk)
                if kept:
                    self._chunks.append(kept)

    @property
    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="replace")


class LocalCommandExecutor(CommandExecutor):
    def run(
        self, request: CommandRequest, cancel: CancelToken | None = None
    ) -> CommandOutcome:
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                list(request.argv),
                cwd=request.cwd,
                env=dict(request.environment),
                stdin=(
                    subprocess.PIPE if request.stdin is not None else subprocess.DEVNULL
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # 二进制管道: 上限是**字节**上限, 按字符数截断算不准, 而且多字节字符
                # 会在块边界被切开.
                start_new_session=True,
            )
        except OSError as exc:
            return CommandOutcome(
                exit_code=None,
                duration_seconds=time.monotonic() - started,
                failure=f"无法启动进程: {exc}",
            )

        budget = _OutputBudget(request.max_output_bytes)
        out_reader = _BoundedReader(process.stdout, budget)
        err_reader = _BoundedReader(process.stderr, budget)
        out_reader.start()
        err_reader.start()
        self._feed_stdin(process, request.stdin)

        timed_out = False
        cancelled = False
        deadline = started + request.timeout_seconds
        try:
            while True:
                if process.poll() is not None:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if cancel is not None and cancel.cancelled:
                    cancelled = True
                    break
                time.sleep(_POLL_SECONDS)
            if timed_out or cancelled:
                self._terminate(process)
            # 读线程要在关管道之前收干: 进程已经退出, 但管道里可能还留着最后几个块.
            out_reader.join(timeout=_GRACE_SECONDS)
            err_reader.join(timeout=_GRACE_SECONDS)
        finally:
            self._reap(process)

        return CommandOutcome(
            exit_code=process.returncode,
            stdout=out_reader.text,
            stderr=err_reader.text,
            timed_out=timed_out,
            cancelled=cancelled,
            truncated=budget.overflowed,
            duration_seconds=time.monotonic() - started,
            child_process_count=1,
        )

    @staticmethod
    def _feed_stdin(process: subprocess.Popen[bytes], stdin: str | None) -> None:
        """喂完就关. 关掉 stdin 是必须的 —— 等着读输入的子进程不会自己退出.

        写入放在读线程**启动之后**: 反过来的话, 一个边读输入边写输出的子进程会在管道
        写满时停下, 而我们还在等它收完输入, 两边互相等.
        """
        if process.stdin is None:
            return
        with contextlib.suppress(OSError, ValueError):
            if stdin:
                process.stdin.write(stdin.encode("utf-8"))
            process.stdin.close()

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        """先 SIGTERM 整组, 宽限期后 SIGKILL.

        不再在这里收输出: 输出由读线程一直在收, 这里只负责让进程停下.
        """
        self._signal_group(process, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_GRACE_SECONDS)
            return
        self._signal_group(process, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_GRACE_SECONDS)

    @staticmethod
    def _signal_group(process: subprocess.Popen[bytes], sig: int) -> None:
        if not hasattr(os, "killpg") or not hasattr(os, "getpgid"):
            with contextlib.suppress(ProcessLookupError, OSError):
                if sig == signal.SIGTERM:
                    process.terminate()
                else:
                    process.kill()
            return
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            # 进程已经退出, 或平台不支持进程组: 退回到单进程信号.
            with contextlib.suppress(ProcessLookupError, OSError):
                process.send_signal(sig)

    @staticmethod
    def _reap(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            LocalCommandExecutor._signal_group(process, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=_GRACE_SECONDS)
        for stream in (process.stdout, process.stderr, process.stdin):
            if stream is not None and not stream.closed:
                stream.close()
