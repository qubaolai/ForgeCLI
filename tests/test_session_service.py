"""SessionService 惰性落盘与事件 / 快照写入（2026-06-27 切片）。

覆盖：start() 不落盘、首个写事件补写 session_created、模式切换推进快照、
event_id 单调、斜杠命令 payload、未 start() 报错、快照 last_event_id 指向末条事件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.session import EventType, SessionService
from forgecli.domain.conversation import TurnStatus
from forgecli.domain.intents import SessionMode
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore
from forgecli.shared.errors import SessionStateError

_CLOCK = "2026-06-27T10:30:00+08:00"
_SID = "20260627T103000-abcd1234"


def _service(home: Path) -> SessionService:
    sessions = home / "sessions"
    return SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work/repo",
        clock=lambda: _CLOCK,
        id_factory=lambda: _SID,
    )


def _events(home: Path) -> list[EventType]:
    return [e.type for e in JsonlEventStore(home / "sessions").read(_SID)]


def test_start_is_lazy_and_writes_no_files(tmp_path: Path) -> None:
    service = _service(tmp_path)

    snapshot = service.start()

    assert snapshot.session_id == _SID
    assert snapshot.mode is SessionMode.ACCEPT_EDITS
    assert snapshot.last_event_id is None
    assert not (tmp_path / "sessions" / _SID).exists()


def test_first_event_flushes_session_created_then_event(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start()

    service.record_user_message("你好", turn_id="turn_0001")

    events = JsonlEventStore(tmp_path / "sessions").read(_SID)
    assert [e.type for e in events] == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
    ]
    assert [e.event_id for e in events] == ["evt_0001", "evt_0002"]
    assert events[1].payload["text"] == "你好"
    state = JsonStateStore(tmp_path / "sessions").read(_SID)
    assert state is not None
    assert state.last_event_id == "evt_0002"
    assert state.mode is SessionMode.ACCEPT_EDITS


def test_mode_change_records_event_and_updates_snapshot(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start()

    service.record_mode_change(SessionMode.PLAN)

    assert service.current().mode is SessionMode.PLAN
    assert _events(tmp_path) == [EventType.SESSION_CREATED, EventType.MODE_CHANGED]
    state = JsonStateStore(tmp_path / "sessions").read(_SID)
    assert state is not None
    assert state.mode is SessionMode.PLAN


def test_event_ids_are_monotonic(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start()

    service.record_user_message("a", turn_id="turn_0001")
    service.record_user_message("b", turn_id="turn_0002")

    ids = [e.event_id for e in JsonlEventStore(tmp_path / "sessions").read(_SID)]
    assert ids == ["evt_0001", "evt_0002", "evt_0003"]


def test_slash_command_records_name_and_args(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start()

    service.record_slash_command("add-dir", ("/extra",))

    last = JsonlEventStore(tmp_path / "sessions").read(_SID)[-1]
    assert last.type is EventType.SLASH_COMMAND
    assert last.payload["command"] == "add-dir"
    assert last.payload["args"] == ["/extra"]


def test_current_before_start_raises(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(SessionStateError):
        service.current()


def test_state_last_event_matches_last_appended(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start()

    service.record_user_message("x", turn_id="turn_0001")
    service.record_slash_command("config")

    events = JsonlEventStore(tmp_path / "sessions").read(_SID)
    state = JsonStateStore(tmp_path / "sessions").read(_SID)
    assert state is not None
    assert state.last_event_id == events[-1].event_id


def test_record_assistant_message_round_trips(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start()

    service.record_user_message("hi", turn_id="turn_0001")
    service.record_assistant_message(
        "yo", turn_id="turn_0001", status=TurnStatus.COMPLETED
    )

    last = JsonlEventStore(tmp_path / "sessions").read(_SID)[-1]
    assert last.type is EventType.ASSISTANT_MESSAGE
    assert last.payload["turn_id"] == "turn_0001"
    assert last.payload["role"] == "assistant"
    assert last.payload["status"] == "completed"
