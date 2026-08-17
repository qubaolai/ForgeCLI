"""写时复制快照后端: APFS clonefile 与 btrfs / XFS reflink.

这两个平台原语做的是同一件事 —— 复制目录树的**元数据**, 内容块共享直到被写入. 所以一份
十万文件的工作区快照占用的磁盘接近零, 耗时只与文件数成正比.

用系统 `cp` 而不是自己走 `clonefile(2)` / `FICLONE`: Python 标准库没有这两个系统调用的
绑定, 而 `cp` 的 `-c` (macOS) 与 `--reflink` (GNU) 就是它们的官方入口, 且**克隆不成立时
会明确失败而不是偷偷退化成整份复制** —— 后者会让一次"快照"悄悄写掉几个 GB.

可用性一律实测: 同一台机器上工作区与快照目录可能落在不同卷, 那时克隆直接失败, 与平台
是什么无关.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import uuid
from pathlib import Path

from forgecli.application.recovery.snapshot_backend import (
    SnapshotHandle,
    WorkspaceSnapshotBackend,
)

__all__ = ["CowSnapshotBackend", "probe_snapshot_backend"]

_PROBE_TIMEOUT_SECONDS = 10.0
_CAPTURE_TIMEOUT_SECONDS = 120.0


class CowSnapshotBackend(WorkspaceSnapshotBackend):
    def __init__(self, snapshot_root: Path) -> None:
        self._root = snapshot_root

    @property
    def name(self) -> str:
        return "copy_on_write"

    def available_for(self, root: str) -> bool:
        """真的克隆一个临时文件试一次.

        只看 `platform.system()` 是不够的: macOS 上跨卷 clonefile 会失败, Linux 上
        ext4 根本不支持 reflink. 探测的成本是一次几字节的克隆, 换来的是不会在真正需要
        快照的时候才发现做不到.
        """
        source = Path(root)
        if not source.is_dir():
            return False
        probe_id = f"probe_{uuid.uuid4().hex[:8]}"
        origin = source / f".forge-{probe_id}"
        target = self._root / probe_id
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            origin.write_bytes(b"forge")
            self._clone(origin, target, timeout=_PROBE_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError):
            return False
        else:
            return True
        finally:
            origin.unlink(missing_ok=True)
            shutil.rmtree(target, ignore_errors=True)

    def capture(self, root: str, snapshot_id: str) -> SnapshotHandle:
        location = self._root / snapshot_id
        location.parent.mkdir(parents=True, exist_ok=True)
        self._clone(Path(root), location, timeout=_CAPTURE_TIMEOUT_SECONDS)
        return SnapshotHandle(
            snapshot_id=snapshot_id,
            backend=self.name,
            location=str(location),
            root=root,
            file_count=sum(1 for item in location.rglob("*") if item.is_file()),
        )

    def restore(self, handle: SnapshotHandle, root: str) -> None:
        """把快照内容覆盖回工作区.

        先写入快照里的每一项, 再删掉快照里不存在而工作区里有的东西 —— 后一步是必须的:
        少了它, "撤销"之后那次调用新建的文件仍然留在树里, 而用户以为已经回到原状.
        """
        source = Path(handle.location)
        if not source.is_dir():
            raise OSError(f"快照不存在或已被清理: {handle.location}")
        destination = Path(root)
        kept: set[Path] = set()
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            kept.add(relative)
            target = destination / relative
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        for item in sorted(destination.rglob("*"), reverse=True):
            relative = item.relative_to(destination)
            if relative in kept:
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    def discard(self, handle: SnapshotHandle) -> None:
        shutil.rmtree(handle.location, ignore_errors=True)

    def _clone(self, source: Path, target: Path, *, timeout: float) -> None:
        """跑一次克隆. 非零退出转成 OSError, 由调用方决定退回哪条路."""
        argv = _clone_argv(source, target)
        if argv is None:
            raise OSError("当前平台没有可用的写时复制命令")
        completed = subprocess.run(  # noqa: S603 - argv 固定, 无 shell
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise OSError(
                f"写时复制失败 (exit={completed.returncode}): "
                f"{completed.stderr.strip() or '无错误输出'}"
            )


def _clone_argv(source: Path, target: Path) -> list[str] | None:
    system = platform.system()
    if system == "Darwin":
        # -c 要求 clonefile; 做不到就报错, 不会退化成整份复制.
        return ["/bin/cp", "-Rc", str(source), str(target)]
    if system == "Linux":
        # `--reflink=always` 而不是 auto: auto 会在不支持时静默整份复制, 于是一次
        # "快照"悄悄写掉几个 GB, 而我们本来是想在那种情况下退回按文件 preimage 的.
        return ["cp", "-R", "--reflink=always", str(source), str(target)]
    return None


def probe_snapshot_backend(
    snapshot_root: Path, workspace_root: str
) -> CowSnapshotBackend | None:
    """启动时探测一次. 拿不到就返回 None, 由恢复层退回复制式 preimage."""
    backend = CowSnapshotBackend(snapshot_root)
    return backend if backend.available_for(workspace_root) else None
