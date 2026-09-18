"""读过的文件 (ADR-0054 决策 5; 对应 Claude Code 的 fileStateCache.ts).

Read 写它, Edit / Write 的 validate_input 读它: 没读过不许改, 读后被改也不许改. 这是
对 TOCTOU 的全部处理.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass

__all__ = [
    "FileState",
    "FileStateCache",
    "READ_FILE_STATE_CACHE_SIZE",
    "normalize_path",
]

READ_FILE_STATE_CACHE_SIZE = 100
_DEFAULT_MAX_CACHE_SIZE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class FileState:
    content: str
    timestamp: float
    offset: int | None = None
    limit: int | None = None
    # 模型只看到了一部分 (注入的 FORGE.md 之类): Edit / Write 要求先显式 Read 一次.
    is_partial_view: bool = False

    @property
    def is_full_read(self) -> bool:
        return self.offset is None and self.limit is None


def normalize_path(path: str) -> str:
    """缓存键的规范化. 要与缓存里的键比较的路径 (工作区根, 目录) 必须走同一个函数."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


_normalize = normalize_path


class FileStateCache:
    """按规范化路径存的 LRU. 条数与字节数两个上限."""

    def __init__(
        self,
        max_entries: int = READ_FILE_STATE_CACHE_SIZE,
        max_size_bytes: int = _DEFAULT_MAX_CACHE_SIZE_BYTES,
    ) -> None:
        self._max_entries = max_entries
        self._max_size_bytes = max_size_bytes
        self._entries: OrderedDict[str, FileState] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, path: str) -> FileState | None:
        key = _normalize(path)
        with self._lock:
            state = self._entries.get(key)
            if state is None:
                return None
            self._entries.move_to_end(key)
            return state

    def set(self, path: str, state: FileState) -> None:
        key = _normalize(path)
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._size -= len(old.content.encode("utf-8"))
            self._entries[key] = state
            self._size += len(state.content.encode("utf-8"))
            while len(self._entries) > self._max_entries or (
                self._size > self._max_size_bytes and len(self._entries) > 1
            ):
                _, evicted = self._entries.popitem(last=False)
                self._size -= len(evicted.content.encode("utf-8"))

    def has(self, path: str) -> bool:
        with self._lock:
            return _normalize(path) in self._entries

    def delete(self, path: str) -> bool:
        key = _normalize(path)
        with self._lock:
            state = self._entries.pop(key, None)
            if state is None:
                return False
            self._size -= len(state.content.encode("utf-8"))
            return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size = 0

    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._entries.keys())
