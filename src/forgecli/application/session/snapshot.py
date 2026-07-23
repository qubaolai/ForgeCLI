"""会话快照值对象：state.json 的内存表示。

state.json 是可由 events.jsonl 重建的快速恢复快照，不是唯一真相源（ADR-0001）。
字段对齐 detailed-design §4.3 的子集：session id、workspace root、mode、
last_event_id、updated_at、status、schema_version，外加 title（会话摘要名称）。

title 是给 /resume 列表/搜索用的人类可读摘要：首次落盘时由 SessionService 取首条
输入文本前若干字符生成一次，之后不再变（缺省 ""）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from forgecli.domain.intents import SessionMode


@dataclass(frozen=True)
class SessionSnapshot:
    """当前 active session 的快照。frozen：状态推进用 dataclasses.replace 产生新值。"""

    session_id: str
    workspace_root: str
    mode: SessionMode
    last_event_id: str | None
    updated_at: str
    status: str = "active"
    schema_version: int = 1
    title: str = ""

    def to_dict(self) -> dict[str, object]:
        """序列化为可 JSON 落盘的纯字典（mode 落字符串值，键名对齐 §4.3）。"""
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "workspace_root": self.workspace_root,
            "current_mode": self.mode.value,
            "status": self.status,
            "title": self.title,
            "last_event_id": self.last_event_id,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> SessionSnapshot:
        """从 state.json 还原快照（供 read / 测试）。"""
        last = data.get("last_event_id")
        raw_version = data.get("schema_version", 1)
        return cls(
            session_id=str(data.get("session_id", "")),
            workspace_root=str(data.get("workspace_root", "")),
            mode=SessionMode(str(data.get("current_mode", SessionMode.ACCEPT_EDITS.value))),
            last_event_id=None if last is None else str(last),
            updated_at=str(data.get("updated_at", "")),
            status=str(data.get("status", "active")),
            schema_version=raw_version if isinstance(raw_version, int) else 1,
            title=str(data.get("title", "")),
        )
