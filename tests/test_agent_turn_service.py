"""AgentTurnService（loop 驱动）：成对落盘 user/assistant、共享 turn_id、失败隔离。"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.agent_loop import (
    AgentLoop,
    AnswerAction,
    LoopDecision,
    LoopInput,
    LoopObservation,
    LoopStepResult,
    LoopStop,
    LoopStopReason,
)
from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.session import EventType, SessionEvent, SessionService
from forgecli.domain.conversation import TurnStatus
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


class _AnswerLoop(AgentLoop):
    """脚本化单步 loop：一次 start 产出固定回答，observe 后正常结束。"""

    def __init__(self, text: str = "好的") -> None:
        self._text = text

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        return LoopDecision(next_action=AnswerAction(text=self._text))

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        return LoopStop.of(LoopStopReason.FINAL_ANSWER)


class _BoomLoop(AgentLoop):
    """start 即抛错的 loop：驱动器必须把异常隔离为 FAILED。"""

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        raise RuntimeError("llm down")

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        raise AssertionError("不应到达 observe")


def _service(session: SessionService, text: str = "好的") -> AgentTurnService:
    return AgentTurnService(session, loop_factory=lambda: _AnswerLoop(text))


def test_handle_user_message_writes_pair_with_shared_turn_id(tmp_path: Path) -> None:
    session = _session(tmp_path)

    response = _service(session).handle_user_message("你好")

    assert response.turn_id == "turn_0001"
    assert response.status is TurnStatus.COMPLETED
    assert response.text == "好的"
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
    assert assistant.payload["stop_reason"] == "final_answer"


def test_pair_order_and_state_points_to_assistant(tmp_path: Path) -> None:
    session = _session(tmp_path)

    _service(session).handle_user_message("x")

    events = _events(tmp_path)
    state = JsonStateStore(tmp_path / "sessions").read(_SID)
    assert events[-1].type is EventType.ASSISTANT_MESSAGE
    assert state is not None
    assert state.last_event_id == events[-1].event_id


def test_turn_id_increments_across_calls(tmp_path: Path) -> None:
    agent = _service(_session(tmp_path))

    first = agent.handle_user_message("a")
    second = agent.handle_user_message("b")

    assert (first.turn_id, second.turn_id) == ("turn_0001", "turn_0002")


def test_loop_failure_is_isolated_as_failed(tmp_path: Path) -> None:
    agent = AgentTurnService(_session(tmp_path), loop_factory=_BoomLoop)

    response = agent.handle_user_message("x")

    assert response.status is TurnStatus.FAILED
    events = _events(tmp_path)
    assert events[-1].type is EventType.ASSISTANT_MESSAGE
    assert events[-1].payload["status"] == "failed"


def test_constructs_with_only_session_facade(tmp_path: Path) -> None:
    # 门面约束：只靠 SessionService + loop 工厂即可构造，不需要 EventStore/StateStore。
    AgentTurnService(_session(tmp_path), loop_factory=_AnswerLoop)
