"""真实工作区快照提供者。"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from forgecli.domain.workspace.changes import WorkspaceFileState, WorkspaceSnapshot
from forgecli.infrastructure.workspace.ignore_oracle import ignore_predicate

__all__ = ["OsWorkspaceSnapshotProvider"]


class OsWorkspaceSnapshotProvider:
    """用 lstat 扫描工作区文件状态，不跟随目录符号链接。"""

    def __init__(self, roots: Callable[[], tuple[str, ...]]) -> None:
        self._roots = roots

    def snapshot(self) -> WorkspaceSnapshot:
        found: dict[str, WorkspaceFileState] = {}
        for raw_root in self._roots():
            root = Path(raw_root).resolve()
            if not root.is_dir():
                continue
            ignored = ignore_predicate(str(root))
            for directory, dirnames, filenames in os.walk(root, followlinks=False):
                relative_directory = os.path.relpath(directory, root)
                relative_directory = (
                    ""
                    if relative_directory == "."
                    else relative_directory.replace(os.sep, "/")
                )
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not ignored(
                        f"{relative_directory}/{name}/"
                        if relative_directory
                        else f"{name}/"
                    )
                ]
                for name in filenames:
                    path = Path(directory) / name
                    relative_path = os.path.relpath(path, root).replace(os.sep, "/")
                    if ignored(relative_path):
                        continue
                    try:
                        stat = path.lstat()
                    except OSError:
                        continue
                    resolved = str(path)
                    found[resolved] = WorkspaceFileState(
                        path=resolved,
                        size=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        file_identity=f"{stat.st_dev}:{stat.st_ino}",
                        mode=stat.st_mode,
                    )
        return WorkspaceSnapshot(files=tuple(found[path] for path in sorted(found)))
