"""AgentRunEventBus 的事件契约 (ADR-0016 §3, §12.1).

总线的全部价值就是这三条性质: 编号单调, 发布者管不着编号, subscriber 崩了不牵连主循环.
少任何一条, "运行时间线可测"就不成立了.
"""

from __future__ import annotations

import pytest

from forgecli.application.agent_run.events import (
    AgentRunEventBus,
    AgentRunEventSubscriber,
)
from forgecli.domain.agent.run_events import (
    AgentRunEvent,
    AgentRunEventKind,
    DecisionSummaryPayload,
    TurnStartedPayload,
)


class Collector(AgentRunEventSubscriber):
    def __init__(self) -> None:
        self.events: list[AgentRunEvent] = []

    def on_event(self, event: AgentRunEvent) -> None:
        self.events.append(event)


class Exploding(AgentRunEventSubscriber):
    def __init__(self) -> None:
        self.calls = 0

    def on_event(self, event: AgentRunEvent) -> None:
        self.calls += 1
        raise RuntimeError("renderer 炸了")


def _publish(bus: AgentRunEventBus, turn: str = "turn-1") -> AgentRunEvent:
    return bus.publish(
        AgentRunEventKind.DECISION_SUMMARY,
        turn_id=turn,
        payload=DecisionSummaryPayload(reason_summary="继续"),
    )


# ---- 编号 ----


def test_sequence_starts_at_one_and_increases() -> None:
    bus = AgentRunEventBus()
    collector = Collector()
    bus.subscribe(collector)

    for _ in range(3):
        _publish(bus)

    assert [event.sequence for event in collector.events] == [1, 2, 3]


def test_each_turn_gets_its_own_counter() -> None:
    bus = AgentRunEventBus()
    collector = Collector()
    bus.subscribe(collector)

    _publish(bus, "turn-1")
    _publish(bus, "turn-1")
    _publish(bus, "turn-2")

    numbered = [(event.turn_id, event.sequence) for event in collector.events]
    assert numbered == [("turn-1", 1), ("turn-1", 2), ("turn-2", 1)]


def test_event_ids_are_unique() -> None:
    bus = AgentRunEventBus()
    ids = {_publish(bus).event_id for _ in range(20)}
    assert len(ids) == 20


def test_envelope_rejects_a_hand_written_sequence_below_one() -> None:
    # 发布者不该自己构造事件; 真这么干时至少要撞上不变量.
    with pytest.raises(ValueError, match="sequence"):
        AgentRunEvent(
            event_id="e1",
            kind=AgentRunEventKind.TURN_STARTED,
            turn_id="turn-1",
            sequence=0,
            occurred_at=0.0,
            payload=TurnStartedPayload(mode="chat", tool_count=0),
        )


def test_envelope_rejects_an_empty_turn_id() -> None:
    with pytest.raises(ValueError, match="turn_id"):
        AgentRunEvent(
            event_id="e1",
            kind=AgentRunEventKind.TURN_STARTED,
            turn_id="  ",
            sequence=1,
            occurred_at=0.0,
        )


# ---- 隔离 ----


def test_a_failing_subscriber_does_not_stop_the_others() -> None:
    bus = AgentRunEventBus()
    exploding = Exploding()
    collector = Collector()
    bus.subscribe(exploding)
    bus.subscribe(collector)

    _publish(bus)

    assert exploding.calls == 1
    assert len(collector.events) == 1, "前一个订阅者抛错不能吞掉后一个"


def test_a_failing_subscriber_does_not_propagate_to_the_publisher() -> None:
    bus = AgentRunEventBus()
    bus.subscribe(Exploding())

    event = _publish(bus)  # 不抛

    assert event.sequence == 1
    assert len(bus.isolated_failures) == 1
    subscriber, failed_event, exc = bus.isolated_failures[0]
    assert failed_event is event
    assert isinstance(exc, RuntimeError)
    assert isinstance(subscriber, Exploding)


def test_numbering_continues_after_a_subscriber_failure() -> None:
    """事件"发生过"与"谁看到了"无关: 分发失败不能让编号停下或回退."""
    bus = AgentRunEventBus()
    bus.subscribe(Exploding())

    assert [_publish(bus).sequence for _ in range(3)] == [1, 2, 3]


def test_subscribers_run_in_registration_order() -> None:
    bus = AgentRunEventBus()
    order: list[str] = []

    class Named(AgentRunEventSubscriber):
        def __init__(self, name: str) -> None:
            self._name = name

        def on_event(self, event: AgentRunEvent) -> None:
            order.append(self._name)

    bus.subscribe(Named("first"))
    bus.subscribe(Named("second"))
    _publish(bus)

    assert order == ["first", "second"]


# ---- 时钟 ----


def test_occurred_at_comes_from_the_injected_clock() -> None:
    ticks = iter([1.0, 2.5])
    bus = AgentRunEventBus(clock=lambda: next(ticks))
    first = _publish(bus)
    second = _publish(bus)
    assert (first.occurred_at, second.occurred_at) == (1.0, 2.5)
