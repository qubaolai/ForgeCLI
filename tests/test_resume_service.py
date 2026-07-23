"""ResumeService：枚举 / 搜索 / 整段读取历史会话（tmp_path + 真 infra）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.session import (
    EventType,
    ResumeService,
    SessionEvent,
    SessionSnapshot,
)
from forgecli.domain.intents import SessionMode
from forgecli.infrastructure.session import (
    FsSessionCatalog,
    JsonlEventStore,
    JsonStateStore,
)
from forgecli.shared.errors import SessionStateError


def _service(sessions: Path) -> ResumeService:
    return ResumeService(
        FsSessionCatalog(sessions),
        JsonStateStore(sessions),
        JsonlEventStore(sessions),
    )


def _make_session(
    sessions: Path,
    session_id: str,
    *,
    title: str,
    updated_at: str,
    workspace_root: str = "/work",
    user_texts: tuple[str, ...] = (),
) -> SessionSnapshot:
    """在磁盘造一个历史会话：写 state.json + events.jsonl。"""
    events = JsonlEventStore(sessions)
    seq = 0

    def emit(event_type: EventType, payload: dict[str, object]) -> str:
        nonlocal seq
        seq += 1
        event_id = f"evt_{seq:04d}"
        events.append(
            SessionEvent(
                event_id=event_id,
                session_id=session_id,
                type=event_type,
                created_at=updated_at,
                payload=payload,
            )
        )
        return event_id

    last = emit(EventType.SESSION_CREATED, {"workspace_root": workspace_root})
    for i, text in enumerate(user_texts):
        turn = f"turn_{i+1:04d}"
        last = emit(EventType.USER_MESSAGE, {"turn_id": turn, "text": text})
        last = emit(EventType.ASSISTANT_MESSAGE, {"turn_id": turn, "text": "ok"})

    snapshot = SessionSnapshot(
        session_id=session_id,
        workspace_root=workspace_root,
        mode=SessionMode.ACCEPT_EDITS,
        last_event_id=last,
        updated_at=updated_at,
        title=title,
    )
    JsonStateStore(sessions).write(snapshot)
    return snapshot


def test_list_sessions_orders_recent_first_and_limits(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _make_session(sessions, "s-old", title="老会话", updated_at="2026-06-27")
    _make_session(sessions, "s-new", title="新会话", updated_at="2026-06-29")
    _make_session(sessions, "s-mid", title="中会话", updated_at="2026-06-28")

    listed = _service(sessions).list_sessions()
    assert [s.session_id for s in listed] == ["s-new", "s-mid", "s-old"]

    assert len(_service(sessions).list_sessions(limit=2)) == 2


def test_list_sessions_filters_by_title_and_id(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _make_session(sessions, "abc-123", title="重构登录", updated_at="2026-06-27")
    _make_session(sessions, "def-456", title="修复缓存", updated_at="2026-06-28")

    svc = _service(sessions)
    assert [s.session_id for s in svc.list_sessions(query="登录")] == ["abc-123"]
    # session_id 子串、大小写不敏感
    assert [s.session_id for s in svc.list_sessions(query="DEF")] == ["def-456"]
    assert svc.list_sessions(query="不存在") == []


def test_load_full_round_trips(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    snapshot = _make_session(
        sessions,
        "s1",
        title="你好",
        updated_at="2026-06-29T10:00:00+08:00",
        user_texts=("你好", "再来一句"),
    )

    loaded, events = _service(sessions).load_full("s1")
    assert loaded == snapshot
    # session_created + 2 * (user + assistant)
    assert len(events) == 5
    assert events[0].type == EventType.SESSION_CREATED
    assert sum(1 for e in events if e.type == EventType.USER_MESSAGE) == 2


def test_has_session_and_load_missing(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _make_session(sessions, "s1", title="x", updated_at="2026-06-29T10:00:00+08:00")

    svc = _service(sessions)
    assert svc.has_session("s1") is True
    assert svc.has_session("nope") is False
    with pytest.raises(SessionStateError):
        svc.load_full("nope")
