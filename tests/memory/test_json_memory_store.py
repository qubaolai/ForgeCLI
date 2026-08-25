"""记忆的落盘往返 (ADR-0033 决策 4).

一份读不了的 memory.json 不该让整个会话起不来 —— 与 ProjectInstructionReader 同一条
理由: 记忆是辅助机制, 少一条的后果只是模型少知道一件事.
"""

from __future__ import annotations

import json
from pathlib import Path

from forgecli.domain.memory.entry import MemoryEntry, MemoryProvenance, MemoryScope
from forgecli.infrastructure.memory.json_memory_store import JsonMemoryStore


def _entry(key: str, value: str) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=value,
        scope=MemoryScope.PROJECT,
        provenance=MemoryProvenance(
            session_id="ses_01",
            turn_id="turn_0003",
            created_at="2026-08-25T10:00:00+08:00",
            derived_from="用户纠正过一次",
        ),
    )


def test_a_saved_entry_reads_back_whole(tmp_path: Path) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json", MemoryScope.PROJECT)

    store.save((_entry("test_command", "make test"),))

    entry = store.load()[0]
    assert (entry.key, entry.value) == ("test_command", "make test")
    assert entry.provenance.derived_from == "用户纠正过一次"


def test_a_missing_file_is_simply_empty(tmp_path: Path) -> None:
    assert JsonMemoryStore(tmp_path / "nope.json", MemoryScope.USER).load() == ()


def test_one_broken_entry_does_not_take_the_others_down(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "entries": [
                    {"key": "不合法的 key", "value": "v"},
                    {"key": "good", "value": "v"},
                    "根本不是对象",
                ]
            }
        ),
        "utf-8",
    )

    entries = JsonMemoryStore(path, MemoryScope.PROJECT).load()

    assert [entry.key for entry in entries] == ["good"]


def test_the_scope_comes_from_where_the_file_lives_not_from_the_file(
    tmp_path: Path,
) -> None:
    """两份说法不一致时, 文件在哪才是事实 —— 文件里的字段可能是手改错的."""
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps({"entries": [{"key": "k", "value": "v", "scope": "project"}]}),
        "utf-8",
    )

    assert JsonMemoryStore(path, MemoryScope.USER).load()[0].scope is MemoryScope.USER
