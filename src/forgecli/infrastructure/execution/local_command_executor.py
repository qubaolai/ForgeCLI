"""本机子进程执行器, 当前唯一的 CommandExecutor 实现.

四条与安全直接相关的实现细节:
1. 不做 PATH 查找: argv[0] 必须是绝对路径, 由调用方在裁决阶段解析并绑定. 这里
   在查一次 PATH, 就等于允许"裁决的是 /usr/bin/python3, 执行的是工作区里那个 python3".
2. 不继承 os.environ: 环境是调用方给的已净化快照.
3. 独立进程组: 超时或取消是整组一起杀, 否则 `bash -c 'sleep 100 &'` 留下的孙进程
   会在 Forge 退出后继续跑.
4. 输出有上限: 超限即截断并如实标记, 不把几百 MB 的日志读进内存.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
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
                text=True,
                errors="replace",
                start_new_session=True,
            )
        except OSError as exc:
            return CommandOutcome(
                exit_code = None,
                duration_seconds = time.monotonic() - started,
                failure = f"无法启动进程: {exc}",
            )
        timed_out = False
        canceled = False
        deadline = started + request.timeout_seconds
        stdout, stderr = "",""
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                if cancel is not None and cancel.cancelled:
                    canceled = True
                    break
                try:
                    stdout, stderr = process.communicate(
                        input=request.stdin, timeout=min(remaining, _POLL_SECONDS)
                    )
                    break
                except subprocess.TimeoutExpired:
                    continue
            if timed_out or canceled:
                stdout, stderr = self._terminate(process)
        finally:
            self._reap(process)

        stdout, out_truncated = _clamp(stdout, request.max_output_bytes)
        stderr, err_truncated = _clamp(stderr, request.max_output_bytes)
        return CommandOutcome(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            cancelled=canceled,
            truncated=out_truncated or err_truncated,
            duration_seconds=time.monotonic() - started,
            child_process_count=1,
        )

    def _terminate(self, process: subprocess.Popen[str]) -> tuple[str, str]:
        """先 SIGTERM 整组, 宽限期后 SIGKILL. 尽力收走已经产生的输出."""
        self._signal_group(process, signal.SIGTERM)
        try:
            return process.communicate(timeout=_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._signal_group(process, signal.SIGKILL)
        try:
            return process.communicate(timeout=_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return "", ""

    @staticmethod
    def _signal_group(process: subprocess.Popen[str], sig: int) -> None:
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            # 进程已经退出, 或平台不支持进程组: 退回到单进程信号.
            with contextlib.suppress(ProcessLookupError, OSError):
                process.send_signal(sig)

    @staticmethod
    def _reap(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            LocalCommandExecutor._signal_group(process, signal.SIGKILL)
        for stream in (process.stdout, process.stderr, process.stdin):
            if stream is not None and not stream.closed:
                stream.close()


def _clamp(text: str, limit: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True