"""工作区变化监测端口与快照差异算法。"""

from __future__ import annotations

from typing import Protocol

from forgecli.domain.workspace.changes import (
    WorkspaceChange,
    WorkspaceChangeKind,
    WorkspaceChangeSource,
    WorkspaceSnapshot,
)

__all__ = [
    "WorkspaceChangeMonitor",
    "WorkspaceSnapshotProvider",
    "diff_workspace_snapshots",
]


class WorkspaceSnapshotProvider(Protocol):
    """取得当前工作区快照的端口。"""

    def snapshot(self) -> WorkspaceSnapshot: ...


class WorkspaceChangeMonitor:
    """保存上一个检查点，并把差异归类为 agent 或 external。"""

    def __init__(self, provider: WorkspaceSnapshotProvider) -> None:
        self._provider = provider
        self._last: WorkspaceSnapshot | None = None

    def start(self) -> None:
        self._last = self._provider.snapshot()

    def checkpoint(self, source: WorkspaceChangeSource) -> tuple[WorkspaceChange, ...]:
        current = self._provider.snapshot()
        if self._last is None:
            self._last = current
            return ()
        changes = diff_workspace_snapshots(self._last, current, source=source)
        self._last = current
        return changes


def diff_workspace_snapshots(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
    *,
    source: WorkspaceChangeSource,
) -> tuple[WorkspaceChange, ...]:
    """比较两个快照，输出稳定排序的文件差异。"""

    old = before.by_path
    new = after.by_path
    changes: list[WorkspaceChange] = []
    for path in sorted(old.keys() | new.keys()):
        previous = old.get(path)
        current = new.get(path)
        if previous is None:
            kind = WorkspaceChangeKind.CREATED
        elif current is None:
            kind = WorkspaceChangeKind.DELETED
        elif previous != current:
            kind = WorkspaceChangeKind.MODIFIED
        else:
            continue
        changes.append(WorkspaceChange(path=path, kind=kind, source=source))
    return tuple(changes)
