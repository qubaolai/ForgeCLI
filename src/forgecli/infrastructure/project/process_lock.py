"""项目级排他锁: 同一个项目只允许同一个 forge 进程操作.

锁文件落在用户级 Forge home 的项目目录下 (projects/<id>/forge.lock), 不写进仓库.
互斥与崩溃自愈都由 OS 咨询锁保证:

    - 原子互斥: 两个进程同时启动只有一个拿到锁;
    - 崩溃自愈: 进程被 kill -9 或断电时由 OS 自动释放, 无陈旧锁残留, 因此不需要 PID
      存活探测, 也没有"要不要抢占陈旧锁"这类判断.

咨询锁本身由 `filelock` 提供 (ADR-0040 决策 4.5). 这里原先是两段平台分支: POSIX 走
`fcntl.flock`, Windows 走 `msvcrt.locking`. 那两段没有一行是 Forge 的产品判断 —— 它们是
"怎么在这个操作系统上拿一把文件锁", 而这个问题别人已经答了, 还答得比我们全 (filelock
还处理了 WSL, NFS 与 SunOS 上的差异, 我们一条都没写).

留在这里的是产品语义: 锁文件位置, 持有者提示的措辞, 以及"已被占用"要抛哪个错.

## 持有者元数据为什么是**另一个文件**

被占用时要能说出"谁正占用", 所以得有 pid / 起始时间 / 主机名. 原先这份元数据写在锁文件
自己里面, 代价是一条平台差异: Windows 上被锁的字节是强制锁, 竞争方读不到, 只能退化成泛化
提示. 换成写在旁边的 `forge.lock.holder.json` 之后, 两个平台读法一致, 那条差异消失.

元数据只在**拿到锁之后**写, 所以竞争方读到的一定是当前持有者. 进程被 kill -9 时元数据会
留下, 但没人会读到它: 只有 acquire 失败的一方才去读, 而 acquire 失败意味着确实有人持锁,
那个人已经把元数据覆盖成自己的了.

判据始终是"锁是否被持有", 不是"文件是否存在" —— 咨询锁释放后空文件留存属正常.
锁粒度为项目 (按 project_id / primary_workspace_root), ``workspace_roots`` 里额外加入的
目录不参与, 跨项目共享子目录的重叠本期不防.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
from datetime import datetime
from pathlib import Path
from types import TracebackType

from filelock import FileLock, Timeout

from forgecli.shared.errors import ForgeError


class ProjectLockedError(ForgeError):
    """项目已被另一 forge 进程占用；message 面向用户、可直接展示。"""

    def __init__(self, message: str, *, pid: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.pid = pid


def _holder_hint(path: Path) -> tuple[str, int | None]:
    """读持有者元数据拼成可读提示; 读不出就泛化兜底.

    读不出是允许的: 元数据只服务诊断, 互斥性不依赖它. 持有者刚拿到锁还没来得及写,
    或者文件被手工删掉, 都会走到这条泛化提示上.
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
        self._holder_path = lock_path.with_name(f"{lock_path.name}.holder.json")
        # timeout=0: 非阻塞. 启动时发现项目被占用要立刻告诉用户, 而不是让终端挂在那里.
        self._lock = FileLock(str(lock_path), timeout=0)

    def acquire(self) -> None:
        """非阻塞取得排他锁；已被占用则抛 ProjectLockedError。"""
        if self._lock.is_locked:
            return  # 同一个实例重复 acquire 幂等
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire()
        except Timeout as ex:
            message, pid = _holder_hint(self._holder_path)
            raise ProjectLockedError(message, pid=pid) from ex
        # 先拿到锁, 再写元数据: 竞争方拿不到锁, 不会生成脏的元数据
        self._write_holder()

    def release(self) -> None:
        """释放锁并清掉持有者元数据；未持有时为 no-op。"""
        if not self._lock.is_locked:
            return
        with contextlib.suppress(OSError):
            self._holder_path.unlink(missing_ok=True)
        # force=True: filelock 默认按重入计数释放, 而这里的 acquire 是幂等的 ——
        # 重复 acquire 不加计数, 那么一次 release 就该真的放开.
        self._lock.release(force=True)

    def _write_holder(self) -> None:
        """原子写: 竞争方永远读到完整的一份, 不会撞上写到一半的 JSON."""
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "host": socket.gethostname(),
            }
        )
        temporary = self._holder_path.with_name(
            f"{self._holder_path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self._holder_path)

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
