"""可丢弃的运行文件统一放在系统临时目录, 与用户持久状态分开.

每个进程占用独立目录和文件锁. 启动时只回收超过七天且未被其他进程占用的临时产物;
不删除运行、项目或会话容器, 未知内容也保留. 系统清理临时目录可能使产物提前失效.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from filelock import FileLock, Timeout

RETENTION_SECONDS = 7 * 24 * 60 * 60
_MARKER = ".forge-runtime"
_LOCK_FILE = ".active.lock"


def _private_root(root: Path) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError("Forge temporary root must be a real directory")
    if hasattr(os, "getuid"):
        if info.st_uid != os.getuid():
            raise OSError("Forge temporary root is owned by another user")
        root.chmod(0o700)


def _real_directories(parent: Path) -> Iterator[Path]:
    """逐层枚举真实子目录, 不通过任一层符号链接进入其他位置."""
    if parent.is_symlink() or not parent.is_dir():
        return
    for child in parent.iterdir():
        if not child.is_symlink() and child.is_dir():
            yield child


def _temporary_directories(run: Path) -> Iterator[Path]:
    """只列出约定的临时产物位置; 不按后缀或递归搜索猜测文件用途."""
    yield run / "logs"
    yield run / "artifacts"
    for child in _real_directories(run):
        if child.name.startswith("sandbox-"):
            yield child
    for project in _real_directories(run / "projects"):
        yield project / "file-history"
        for session in _real_directories(project / "sessions"):
            yield session / "tool-results"
            yield session / "diagnostics"
            # 子代理临时报告; 持久会话记录位于 Forge home, 不在这里.
            yield session / "agents"


def cleanup_stale_artifacts(root: Path, *, now: float | None = None) -> None:
    """在过期且非活跃的临时运行中按白名单回收产物, 保留历史与未知内容."""
    cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
    for candidate in root.glob("run-*"):
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            if not (candidate / _MARKER).is_file():
                continue
            if candidate.stat().st_mtime >= cutoff:
                continue
            lease = FileLock(candidate / _LOCK_FILE, timeout=0)
            with lease:
                # 持锁期间重新检查, 避免恰好退出的进程刷新时间后仍被删除.
                if candidate.stat().st_mtime >= cutoff:
                    continue
                # 锁文件留在运行容器中, 整个清理过程均持锁.
                for temporary in _temporary_directories(candidate):
                    if temporary.is_symlink() or not temporary.is_dir():
                        continue
                    try:
                        shutil.rmtree(temporary)
                    except OSError:
                        continue
        except (OSError, Timeout):
            continue


class RuntimeFiles:
    """一次进程运行的目录及存活锁; close 后目录仍按保留期供排错使用."""

    def __init__(self, root: Path) -> None:
        _private_root(root)
        cleanup_stale_artifacts(root)
        self.path = Path(tempfile.mkdtemp(prefix="run-", dir=root))
        self._lease = FileLock(self.path / _LOCK_FILE, thread_local=False)
        self._lease.acquire()
        (self.path / _MARKER).write_text("1\n", encoding="ascii")
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.path.touch(exist_ok=True)
        except OSError:
            pass
        finally:
            self._lease.release()


_runtime: RuntimeFiles | None = None
_guard = threading.Lock()


def runtime_dir() -> Path:
    """返回当前进程的私有临时目录; 并发调用共用同一实例."""
    global _runtime
    with _guard:
        if _runtime is None:
            user = hashlib.sha256(str(Path.home()).encode()).hexdigest()[:12]
            _runtime = RuntimeFiles(Path(tempfile.gettempdir()) / f"forge-{user}")
            atexit.register(_runtime.close)
        return _runtime.path
