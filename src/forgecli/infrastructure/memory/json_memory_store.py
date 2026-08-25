"""记忆的 JSON 落盘.

落在 Forge 状态目录而不是工作区, 与 `plans/` 同一条理由 (ADR-0022 决策 5): 落工作区会
污染用户仓库, 还会被下一轮 Agent 当成项目内容读回上下文. 附带后果是 `fs_*` 工具够不到
它 —— 这正是想要的隔离.

不做 revision 历史 (ADR-0033 决策 4): 记忆写坏的后果上限很低, 它进不了裁决, 最坏是模型
照着一条错事实做了个错判断, 而那一步会当场失败. 用一套本身也会出错的历史机制去兜一个
后果很轻的错误, 不划算. 恢复路径是用户手动删这个文件.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forgecli.application.memory.memory_store import MemoryStore
from forgecli.domain.memory.entry import MemoryEntry, MemoryProvenance, MemoryScope
from forgecli.infrastructure.json_io import read_document, write_document

__all__ = ["JsonMemoryStore"]

_ENTRIES = "entries"


class JsonMemoryStore(MemoryStore):
    def __init__(self, path: Path, scope: MemoryScope) -> None:
        self._path = path
        self._scope = scope

    def load(self) -> tuple[MemoryEntry, ...]:
        raw = read_document(self._path).get(_ENTRIES, [])
        if not isinstance(raw, list):
            return ()
        loaded: list[MemoryEntry] = []
        for item in raw:
            entry = self._entry_of(item)
            # 读不动的条目跳过而不是抛错: 一条坏掉的记忆不该让整个会话起不来, 而
            # "少一条记忆"的后果只是模型少知道一件事.
            if entry is not None:
                loaded.append(entry)
        return tuple(loaded)

    def save(self, entries: tuple[MemoryEntry, ...]) -> None:
        document = read_document(self._path)
        document[_ENTRIES] = [_payload_of(entry) for entry in entries]
        write_document(self._path, document)

    def _entry_of(self, item: Any) -> MemoryEntry | None:
        if not isinstance(item, dict):
            return None
        try:
            return MemoryEntry(
                key=str(item.get("key", "")),
                value=str(item.get("value", "")),
                # scope 取自这个 store 是谁, 不读文件里的字段: 两份说法不一致时, 文件
                # 在哪里才是事实 —— 而文件里的字段可能是手改错的.
                scope=self._scope,
                provenance=MemoryProvenance(
                    session_id=str(item.get("session_id", "")),
                    turn_id=str(item.get("turn_id", "")),
                    created_at=str(item.get("created_at", "")),
                    derived_from=str(item.get("derived_from", "")),
                ),
            )
        except ValueError:
            return None


def _payload_of(entry: MemoryEntry) -> dict[str, object]:
    return {
        "key": entry.key,
        "value": entry.value,
        "session_id": entry.provenance.session_id,
        "turn_id": entry.provenance.turn_id,
        "created_at": entry.provenance.created_at,
        "derived_from": entry.provenance.derived_from,
    }
