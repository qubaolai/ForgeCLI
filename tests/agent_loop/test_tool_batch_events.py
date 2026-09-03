"""模型一次请求多个工具时，整批必须先于首个执行动作对观察端可见。"""

from forgecli.domain.agent.actions import (
    LoopObservation,
    ObservationDisposition,
    ObservationSource,
    ToolRequestAction,
)
from forgecli.domain.agent.run_events import AgentRunEventKind
from forgecli.domain.agent.stop import LoopStopReason
from support.loop_harness import ScriptedGateway, call, loop_input, loop_with, response


def test_complete_tool_batch_is_announced_before_first_dispatch() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(
                tool_calls=(
                    call("fs_read", "call-1"),
                    call("search_text", "call-2"),
                    call("shell_run", "call-3"),
                )
            )
        ]
    )
    loop, events = loop_with(gateway)

    first = loop.start(loop_input())

    assert isinstance(first, ToolRequestAction)
    assert first.request.tool_call_id == "call-1"
    queued = events.of(AgentRunEventKind.TOOL_QUEUED)
    assert [event.tool_call_id for event in queued] == ["call-1", "call-2", "call-3"]
    assert [event.payload.queue_position for event in queued] == [2, 1, 0]

    second = loop.observe(
        LoopObservation(content="ok", source=ObservationSource.TOOL)
    )

    assert isinstance(second, ToolRequestAction)
    assert second.request.tool_call_id == "call-2"
    assert len(events.of(AgentRunEventKind.TOOL_QUEUED)) == 3


def test_abandoned_calls_receive_a_visible_not_run_terminal_event() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(
                tool_calls=(
                    call("fs_read", "call-1"),
                    call("search_text", "call-2"),
                )
            )
        ]
    )
    loop, events = loop_with(gateway)
    loop.start(loop_input())

    stopped = loop.observe(
        LoopObservation(
            content="等待用户决定",
            source=ObservationSource.TOOL,
            disposition=ObservationDisposition.AWAIT_USER_DECISION,
        )
    )

    assert stopped.reason is LoopStopReason.WAIT_PLAN_REVIEW
    cancelled = events.of(AgentRunEventKind.TOOL_CANCELLED)
    assert [event.tool_call_id for event in cancelled] == ["call-2"]
    assert cancelled[0].payload.status == "not_run"
    assert cancelled[0].payload.executed is False
