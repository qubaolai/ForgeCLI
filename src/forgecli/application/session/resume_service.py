"""ResumeService：历史会话的只读服务（列表 / 搜索 / 读取）。

职责边界：
    - 只读模型，不持有活动会话状态——真正的「重指向 + 续写」由 SessionService.resume
      与 AgentTurnService.resume 完成（由 sessions/{id}/resume 路由编排）。
    - list_sessions 供 GET /api/v1/sessions 的列表与关键字搜索；
      load_full 供恢复时一次读出快照 + 全部事件（用于 hydrate 计算，本切片不回放对话）。

依赖三个会话存储抽象：SessionCatalog 枚举 id、StateStore 读快照、EventStore 读事件。
"""

from __future__ import annotations

from forgecli.application.session.event_store import EventStore
from forgecli.application.session.session_catalog import SessionCatalog
from forgecli.application.session.state_store import StateStore
from forgecli.domain.session.events import SessionEvent
from forgecli.domain.session.snapshot import SessionSnapshot
from forgecli.shared.errors import SessionStateError

_DEFAULT_LIMIT = 20


class ResumeService:
    """历史会话的只读视图：枚举 / 搜索 / 整段读取。"""

    def __init__(
        self,
        catalog: SessionCatalog,
        state_store: StateStore,
        event_store: EventStore,
    ) -> None:
        self._catalog = catalog
        self._states = state_store
        self._events = event_store

    def list_sessions(
        self, query: str | None = None, *, limit: int = _DEFAULT_LIMIT
    ) -> list[SessionSnapshot]:
        """枚举可恢复会话，按 updated_at 倒序（最近在前），最多 limit 条。

        给定 query 时按子串过滤（匹配 title 或 session_id，大小写不敏感）。
        """
        snapshots: list[SessionSnapshot] = []
        for session_id in self._catalog.list_session_ids():
            snapshot = self._states.read(session_id)
            if snapshot is not None and _matches(snapshot, query):
                snapshots.append(snapshot)
        snapshots.sort(key=lambda s: s.updated_at, reverse=True)
        return snapshots[:limit]

    def load_full(
        self, session_id: str
    ) -> tuple[SessionSnapshot, tuple[SessionEvent, ...]]:
        """一次读出某会话的快照 + 全部事件；不存在抛 SessionStateError。"""
        snapshot = self._states.read(session_id)
        if snapshot is None:
            raise SessionStateError(f"未找到会话 {session_id}。")
        return snapshot, tuple(self._events.read(session_id))


def _matches(snapshot: SessionSnapshot, query: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return (
        needle in snapshot.title.casefold() or needle in snapshot.session_id.casefold()
    )
