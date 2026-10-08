"""文件历史 (ADR-0054 决策 5; 对应 utils/fileHistory.ts).

Edit / Write 落盘前调 `track_edit(path)` 备份原件; 每条 assistant 消息开始时
`make_snapshot(message_id)`; `rewind(message_id)` 回到那条消息之前的文件状态. 快照
上限 100. Bash 改的文件不在历史里.
"""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

from forgecli.common.clock import now_iso
from forgecli.common.observability.log import get_log

__all__ = ["FileHistory", "FileHistorySnapshot", "RewindOutcome"]

_log = get_log(__name__)
MAX_SNAPSHOTS = 100


@dataclass(frozen=True)
class FileHistorySnapshot:
    message_id: str
    # 路径 -> 备份文件名 (None = 那时文件不存在).
    backups: dict[str, str | None]
    created_at: str


@dataclass(frozen=True)
class RewindOutcome:
    restored: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


@dataclass
class FileHistory:
    """一个会话的文件历史. 索引仅存内存, 备份使用当前进程的临时目录, 与工作区分开."""

    backup_dir: Path
    tracked: dict[str, str | None] = field(default_factory=dict)
    snapshots: list[FileHistorySnapshot] = field(default_factory=list)
    _versions: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def track_edit(self, path: str) -> None:
        """第一次改某个文件前记下它原来的样子. 已经跟踪的文件不重复备份."""
        absolute = os.path.abspath(path)
        with self._lock:
            if absolute in self.tracked:
                return
            self.tracked[absolute] = self._backup(absolute)

    def _backup(self, absolute: str) -> str | None:
        if not os.path.exists(absolute):
            return None
        version = self._versions.get(absolute, 0) + 1
        self._versions[absolute] = version
        name = f"{_slug(absolute)}.v{version}"
        target = self.backup_dir / name
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(absolute, target)
        except OSError:
            _log.exception("file_history.backup_failed", path=absolute)
            return None
        return name

    def make_snapshot(self, message_id: str) -> None:
        """每条 assistant 消息一个快照: 记下此刻每个已跟踪文件的最新备份."""
        with self._lock:
            backups: dict[str, str | None] = {}
            for absolute in self.tracked:
                backups[absolute] = self._backup(absolute)
            self.snapshots.append(
                FileHistorySnapshot(
                    message_id=message_id, backups=backups, created_at=now_iso()
                )
            )
            while len(self.snapshots) > MAX_SNAPSHOTS:
                self.snapshots.pop(0)

    def can_restore(self, message_id: str) -> bool:
        return any(item.message_id == message_id for item in self.snapshots)

    def rewind(self, message_id: str) -> RewindOutcome:
        """回到某条消息之前的状态: 每个跟踪过的文件取那时的备份, 没有的删掉."""
        with self._lock:
            snapshot = None
            for item in self.snapshots:
                if item.message_id == message_id:
                    snapshot = item
                    break
            if snapshot is None:
                return RewindOutcome()
            restored: list[str] = []
            removed: list[str] = []
            failed: list[str] = []
            for absolute, original in self.tracked.items():
                backup = snapshot.backups.get(absolute, original)
                try:
                    if backup is None:
                        if os.path.exists(absolute):
                            os.remove(absolute)
                            removed.append(absolute)
                    else:
                        Path(absolute).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(self.backup_dir / backup, absolute)
                        restored.append(absolute)
                except OSError:
                    _log.exception("file_history.restore_failed", path=absolute)
                    failed.append(absolute)
            return RewindOutcome(
                restored=tuple(restored), removed=tuple(removed), failed=tuple(failed)
            )


def _slug(path: str) -> str:
    return path.strip(os.sep).replace(os.sep, "__").replace(":", "_")
