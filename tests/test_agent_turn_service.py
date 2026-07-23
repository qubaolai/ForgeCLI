"""AgentTurnService stub：成对落盘 user/assistant、共享 turn_id、带 role/status。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.session import EventType, SessionEvent, SessionService
from forgecli.domain.conversation import TurnStatus
from forgecli.domain.intents import SessionMode
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore

_SID = "20260629T100000-abcd1234"


def _session(tmp_path: Path) -> SessionService:
    sessions = tmp_path / "sessions"
    service = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        clock=lambda: "2026-06-29T10:00:00+08:00",
        id_factory=lambda: _SID,
    )
    service.start()
    return service


def _events(tmp_path: Path) -> list[SessionEvent]:
    return JsonlEventStore(tmp_path / "sessions").read(_SID)


def test_handle_user_message_writes_pair_with_shared_turn_id(tmp_path: Path) -> None:
    session = _session(tmp_path)

    response = AgentTurnService(session).handle_user_message("你好")

    assert response.turn_id == "turn_0001"
    assert response.status is TurnStatus.COMPLETED
    assert "chat" in response.text  # 默认 chat 模式
    events = _events(tmp_path)
    assert [e.type for e in events] == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
    ]
    user, assistant = events[1], events[2]
    assert user.payload["turn_id"] == assistant.payload["turn_id"] == "turn_0001"
    assert user.payload["role"] == "user"
    assert assistant.payload["role"] == "assistant"
    assert assistant.payload["status"] == "completed"


@pytest.mark.parametrize("mode", [SessionMode.ACCEPT_EDITS, SessionMode.PLAN, SessionMode.AUTO])
def test_stub_reply_reflects_mode_read_from_session(
    tmp_path: Path, mode: SessionMode
) -> None:
    session = _session(tmp_path)
    if mode is not SessionMode.ACCEPT_EDITS:
        session.record_mode_change(mode)

    response = AgentTurnService(session).handle_user_message("看看")

    assert mode.value in response.text
    assert session.current().mode is mode


def test_pair_order_and_state_points_to_assistant(tmp_path: Path) -> None:
    session = _session(tmp_path)

    AgentTurnService(session).handle_user_message("x")

    events = _events(tmp_path)
    state = JsonStateStore(tmp_path / "sessions").read(_SID)
    assert events[-1].type is EventType.ASSISTANT_MESSAGE
    assert state is not None
    assert state.last_event_id == events[-1].event_id


def test_turn_id_increments_across_calls(tmp_path: Path) -> None:
    agent = AgentTurnService(_session(tmp_path))

    first = agent.handle_user_message("a")
    second = agent.handle_user_message("b")

    assert (first.turn_id, second.turn_id) == ("turn_0001", "turn_0002")


def test_reply_failure_is_isolated_as_failed(tmp_path: Path) -> None:
    def boom(text: str, mode: SessionMode) -> str:
        raise RuntimeError("llm down")

    response = AgentTurnService(_session(tmp_path), reply=boom).handle_user_message("x")

    assert response.status is TurnStatus.FAILED
    events = _events(tmp_path)
    assert events[-1].type is EventType.ASSISTANT_MESSAGE
    assert events[-1].payload["status"] == "failed"


def test_constructs_with_only_session_facade(tmp_path: Path) -> None:
    # 门面约束：只靠 SessionService 即可构造，不需要 EventStore/StateStore。
    AgentTurnService(_session(tmp_path))
