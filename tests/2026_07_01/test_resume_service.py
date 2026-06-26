"""2026-07-01：ResumeService 的历史会话枚举 / 搜索 / 读取。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.session.events import EventType, SessionEvent
from forgecli.application.session.resume_service import ResumeService
from forgecli.application.session.snapshot import SessionSnapshot
from forgecli.domain.intents import SessionMode
from forgecli.infrastructure.session.fs_session_catalog import FsSessionCatalog
from forgecli.infrastructure.session.json_state_store import JsonStateStore
from forgecli.infrastructure.session.jsonl_event_store import JsonlEventStore
from forgecli.shared.errors import SessionStateError


def _snapshot(
    session_id: str,
    *,
    title: str,
    updated_at: str,
    last_event_id: str = "evt_0001",
) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        workspace_root="/repo",
        mode=SessionMode.CHAT,
        last_event_id=last_event_id,
        updated_at=updated_at,
        title=title,
    )


def _event(session_id: str, event_id: str, text: str) -> SessionEvent:
    return SessionEvent(
        event_id=event_id,
        session_id=session_id,
        type=EventType.USER_MESSAGE,
        created_at="2026-07-01T10:00:00+08:00",
        payload={"turn_id": "turn_0001", "role": "user", "text": text},
    )


def _service(tmp_path: Path) -> tuple[ResumeService, JsonStateStore, JsonlEventStore]:
    sessions_dir = tmp_path / "sessions"
    states = JsonStateStore(sessions_dir)
    events = JsonlEventStore(sessions_dir)
    return ResumeService(FsSessionCatalog(sessions_dir), states, events), states, events


def test_list_sessions_sorts_recent_first_filters_and_skips_dirty_dirs(
    tmp_path: Path,
) -> None:
    service, states, _events = _service(tmp_path)
    sessions_dir = tmp_path / "sessions"
    states.write(
        _snapshot(
            "ses_old",
            title="Alpha task",
            updated_at="2026-07-01T09:00:00+08:00",
        )
    )
    states.write(
        _snapshot(
            "ses_recent",
            title="Resume Target",
            updated_at="2026-07-01T12:00:00+08:00",
        )
    )
    (sessions_dir / "dirty_only_events").mkdir(parents=True)
    (sessions_dir / "dirty_only_events" / "events.jsonl").write_text(
        "", encoding="utf-8"
    )

    assert [s.session_id for s in service.list_sessions()] == [
        "ses_recent",
        "ses_old",
    ]
    assert [s.session_id for s in service.list_sessions("target")] == ["ses_recent"]
    assert [s.session_id for s in service.list_sessions("OLD")] == ["ses_old"]
    assert [s.session_id for s in service.list_sessions(limit=1)] == ["ses_recent"]


def test_load_full_returns_snapshot_and_events_or_raises(tmp_path: Path) -> None:
    service, states, events = _service(tmp_path)
    states.write(
        _snapshot(
            "ses_resume",
            title="Resume me",
            updated_at="2026-07-01T10:00:00+08:00",
            last_event_id="evt_0002",
        )
    )
    events.append(_event("ses_resume", "evt_0001", "first"))
    events.append(_event("ses_resume", "evt_0002", "second"))

    snapshot, history = service.load_full("ses_resume")

    assert snapshot.title == "Resume me"
    assert [event.event_id for event in history] == ["evt_0001", "evt_0002"]
    with pytest.raises(SessionStateError, match="未找到会话 missing"):
        service.load_full("missing")
