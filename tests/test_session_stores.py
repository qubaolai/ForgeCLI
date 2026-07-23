"""JsonlEventStore / JsonStateStore round-trip 与原子写（2026-06-27 切片）。"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.session import EventType, SessionEvent, SessionSnapshot
from forgecli.domain.intents import SessionMode
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore


def _event(event_id: str, event_type: EventType) -> SessionEvent:
    return SessionEvent(
        event_id=event_id,
        session_id="s1",
        type=event_type,
        created_at="2026-06-27T10:30:00+08:00",
        payload={"k": "v"},
    )


def test_event_store_is_append_only(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path)

    store.append(_event("evt_0001", EventType.SESSION_CREATED))
    store.append(_event("evt_0002", EventType.USER_MESSAGE))

    lines = (tmp_path / "s1" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_event_store_read_round_trips(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path)
    original = _event("evt_0001", EventType.USER_MESSAGE)

    store.append(original)

    assert store.read("s1") == [original]


def test_event_store_read_missing_returns_empty(tmp_path: Path) -> None:
    assert JsonlEventStore(tmp_path).read("nope") == []


def test_state_store_round_trips(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path)
    snapshot = SessionSnapshot(
        session_id="s1",
        workspace_root="/repo",
        mode=SessionMode.AUTO,
        last_event_id="evt_0009",
        updated_at="2026-06-27T10:30:00+08:00",
    )

    store.write(snapshot)

    assert store.read("s1") == snapshot


def test_state_store_missing_returns_none(tmp_path: Path) -> None:
    assert JsonStateStore(tmp_path).read("nope") is None


def test_state_write_leaves_no_tmp_file(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path)
    snapshot = SessionSnapshot(
        session_id="s1",
        workspace_root="/repo",
        mode=SessionMode.ACCEPT_EDITS,
        last_event_id=None,
        updated_at="t",
    )

    store.write(snapshot)

    assert list((tmp_path / "s1").glob("*.tmp")) == []
