from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    TurnStartedPayload,
)
from forgecli.interfaces.web.events import WebEventHub


def test_event_hub_preserves_domain_order_and_assigns_web_cursor() -> None:
    bus = AgentRunEventBus(event_id_factory=lambda: "event")
    hub = WebEventHub(max_events=4)
    bus.subscribe(hub)

    bus.publish(
        AgentRunEventKind.TURN_STARTED,
        turn_id="turn_0001",
        payload=TurnStartedPayload(mode="plan", tool_count=3),
    )
    bus.publish(
        AgentRunEventKind.TURN_STARTED,
        turn_id="turn_0002",
        payload=TurnStartedPayload(mode="accept_edits", tool_count=8),
    )

    resync, events = hub.after(0)

    assert resync is False
    assert [item.cursor for item in events] == [1, 2]
    assert [item.event_id for item in events] == ["turn_0001:1", "turn_0002:1"]
    assert events[0].data["payload"] == {"mode": "plan", "tool_count": 3}


def test_event_hub_requests_resync_after_ring_buffer_expires() -> None:
    bus = AgentRunEventBus()
    hub = WebEventHub(max_events=1)
    bus.subscribe(hub)
    for turn in ("turn_0001", "turn_0002"):
        bus.publish(
            AgentRunEventKind.TURN_STARTED,
            turn_id=turn,
            payload=TurnStartedPayload(mode="plan", tool_count=0),
        )

    assert hub.after(0) == (True, ())
