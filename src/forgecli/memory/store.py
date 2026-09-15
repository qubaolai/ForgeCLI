"""记忆的读写端口与上限 (ADR-0033 决策 4).

一个 scope 一份存储. 读写规则 (多大算超限, 能存几条) 属 application; 真的去碰文件
系统属 infrastructure.

上限写在这里而不是实现里: 它们是**决策**不是实现细节, 换一个 store 实现不该顺带换掉
"单条 512 字节, 每级 32 条"这条约束. 与 ADR-0018 把 FORGE.md 的上限写在 reader 端口
上是同一个理由.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forgecli.common.json_io import read_document, write_document
from forgecli.memory.entry import MemoryEntry, MemoryProvenance, MemoryScope

__all__ = [
    "JsonMemoryStore",
    "MAX_ENTRIES_PER_SCOPE",
    "MAX_VALUE_BYTES",
]

# 单条记忆的正文上限.
#
# 512 字节而不是更宽: 记忆**每一轮都进提示词**, 与 FORGE.md 那 32 KiB 的一次性预算
# 不是一回事. 一条写成小作文的记忆会在此后每一次模型调用上收费.
MAX_VALUE_BYTES = 512

# 每级最多存几条. 满了拒绝写入并告诉模型先 memory_forget 一条.
#
# 拒绝而不是自动淘汰最旧的: 淘汰要挑一个"最不重要"的, 而这里没有任何依据能挑对 ——
# 最旧的那条完全可能是最要紧的那条 (项目的测试命令一次就记对了, 之后再没动过).
MAX_ENTRIES_PER_SCOPE = 32


# ---- 原 infrastructure/memory/json_memory_store.py ----
# 记忆的 JSON 落盘.
#
# 落在 Forge 状态目录而不是工作区, 与 `plans/` 同一条理由 (ADR-0022 决策 5): 落工作区会
# 污染用户仓库, 还会被下一轮 Agent 当成项目内容读回上下文. 附带后果是 `fs_*` 工具够不到
# 它 —— 这正是想要的隔离.
#
# 不做 revision 历史 (ADR-0033 决策 4): 记忆写坏的后果上限很低, 它进不了裁决, 最坏是模型
# 照着一条错事实做了个错判断, 而那一步会当场失败. 用一套本身也会出错的历史机制去兜一个
# 后果很轻的错误, 不划算. 恢复路径是用户手动删这个文件.
_ENTRIES = "entries"


class JsonMemoryStore:
    """一个 scope 的记忆存储."""

    def __init__(self, path: Path, scope: MemoryScope) -> None:
        self._path = path
        self._scope = scope

    def load(self) -> tuple[MemoryEntry, ...]:
        """读出全部记忆. 读不到, 格式坏掉的一律当作空, 不抛错.

        记忆是辅助机制: 一份读不了的 memory.json 不该让整个会话起不来 (与
        FsProjectInstructionReader 同一条理由).
        """
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
        """整表写回."""
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
