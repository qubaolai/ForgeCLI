"""项目级排他锁: 同一个项目只允许同一个forge进程操作

锁文件落在用户级 Forge home 的项目目录下(projects/<id>/forge.lock)，不写进
仓库。用 OS 咨询锁(POSIX ``fcntl.flock`` / Windows ``msvcrt.locking``)保证：
    - 原子互斥：两个进程同时启动只有一个拿到锁;
    - 崩溃自愈：进程被 kill -9 / 断电时由 OS 自动释放，无陈旧锁残留，
      因此无需 PID 存活探测、也没有"要不要抢占陈旧锁"这类判断。

锁文件里写入持有者元数据(pid / 起始时间 / 主机名)仅用于诊断：被占用时给出
"谁正占用"的可读提示。判据始终是"锁是否被持有"，不是"文件是否存在"——咨询锁
释放后空文件留存属正常。锁粒度为项目(按 project_id / primary_workspace_root),
``workspace_roots`` 里额外加入的目录不参与，跨项目共享子目录的重叠本期不防。
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from types import TracebackType

from forgecli.shared.errors import ForgeError


class ProjectLockedError(ForgeError):
    """项目已被另一 forge 进程占用；message 面向用户、可直接展示。"""

    def __init__(self, message: str, *, pid: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.pid = pid


def _holder_hint(path: Path) -> tuple[str, int | None]:
    """读锁文件里的持有者元数据拼成可读提示; 读不出就泛化兜底

    在 windows 上锁定区间是强制锁, 竞争方可能读不到被锁字节, 此时自然退化为
    泛化提示--排他性不受影响, 只是少了 pid 的诊断信息
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = int(data["pid"])
        started = data.get("started_at", "?")
        host = data.get("host", "?")
        return (f"Forge 已在该项目运行（pid {pid}，自 {started}，主机 {host}）。", pid)
    except (OSError, ValueError, KeyError, TypeError):
        return ("Forge 已在该项目运行（另一个进程正占用该项目）。", None)


class ProcessLock:
    """单项目排他锁，跨平台 OS 咨询锁；可作为上下文管理器使用。"""

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._fd: int | None = None

    def acquire(self) -> None:
        """非阻塞取得排他锁；已被占用则抛 ProjectLockedError。"""
        if self._fd is not None:
            return  # 同一个实例重复 acquire 幂等
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _lock_exclusive_nonblocking(fd)
        except OSError as ex:
            os.close(fd)
            message, pid = _holder_hint(self._path)
            raise ProjectLockedError(message, pid=pid) from ex
        # 先拿到锁, 再写元数据: 竞争方拿不到锁, 不会生成脏的元数据
        self._fd = fd
        self._write_metadata(fd)

    def release(self) -> None:
        """释放锁并关闭文件描述符；未持有时为 no-op。"""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def _write_metadata(self, fd: int) -> None:
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "host": socket.gethostname(),
            }
        ).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, payload)
        os.fsync(fd)

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


if sys.platform == "win32":
    import msvcrt

    def _lock_exclusive_nonblocking(fd: int) -> None:
        # 锁定首字节即表达"占用"；非阻塞，被占用抛 OSError。
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        with contextlib.suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_exclusive_nonblocking(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
