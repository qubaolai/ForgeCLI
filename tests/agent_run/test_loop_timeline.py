"""BuiltinAgentLoop 发出的运行时间线 (ADR-0016 §4.1, §4.2, §12.2).

钉两件事:

- **顺序与分组**: 一轮里每次模型调用有独立 request_id, 起止成对; 多次调用不会串成一段.
- **发布边界**: 循环只报它自己知道的事. 工具被裁决与执行之后的事实 (POLICY_RESOLVED,
  TOOL_STARTED, TOOL_COMPLETED) 由协调器发布 —— 循环在这里编就是猜.
"""

from __future__ import annotations

from types import MappingProxyType

from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopDecision,
    LoopObservation,
    ObservationSource,
    ToolRequestAction,
)
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    ModelCompletedPayload,
    ModelStartedPayload,
    ReasoningStatus,
    ReasoningStatusPayload,
    RunPhase,
    StepStartedPayload,
    TextDeltaPayload,
    ToolQueuedPayload,
    TurnFinishedPayload,
    TurnStartedPayload,
)
from forgecli.domain.tool.tool_call import ToolCall
from support.loop_harness import (
    ScriptedGateway,
    call,
    loop_input,
    loop_with,
    response,
)

K = AgentRunEventKind


# ---- 纯对话轮 ----


def test_a_plain_answer_turn_emits_start_model_answer_and_completion() -> None:
    loop, collector = loop_with(ScriptedGateway(responses=[response("你好呀")]))

    step = loop.start(loop_input())
    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, AnswerAction)
    loop.observe(
        LoopObservation(content="answer_delivered", source=ObservationSource.CONTEXT)
    )

    assert collector.kinds() == [
        K.TURN_STARTED,
        K.STEP_STARTED,
        K.MODEL_STARTED,
        K.MODEL_REASONING_STATUS,
        K.MODEL_OUTPUT_DELTA,
        K.MODEL_REASONING_STATUS,
        K.MODEL_COMPLETED,
        K.MODEL_USAGE,
        K.STEP_STARTED,
        K.DECISION_SUMMARY,
        K.TURN_COMPLETED,
    ]


def test_turn_started_reports_mode_and_tool_count() -> None:
    loop, collector = loop_with(ScriptedGateway(responses=[response("ok")]))
    loop.start(loop_input())

    payload = collector.of(K.TURN_STARTED)[0].payload
    assert isinstance(payload, TurnStartedPayload)
    assert payload.mode == "workspace_write/always"
    assert payload.tool_count == 3


def test_reasoning_status_brackets_the_model_call() -> None:
    """供应商没给可展示摘要时只报状态, 不伪造思维链 (§6)."""
    loop, collector = loop_with(ScriptedGateway(responses=[response("ok")]))
    loop.start(loop_input())

    statuses = [
        event.payload.status
        for event in collector.of(K.MODEL_REASONING_STATUS)
        if isinstance(event.payload, ReasoningStatusPayload)
    ]
    assert statuses == [ReasoningStatus.STARTED, ReasoningStatus.COMPLETED]


def test_visible_text_goes_out_as_an_output_delta() -> None:
    loop, collector = loop_with(ScriptedGateway(responses=[response("你好呀")]))
    loop.start(loop_input())

    payload = collector.of(K.MODEL_OUTPUT_DELTA)[0].payload
    assert isinstance(payload, TextDeltaPayload)
    assert payload.text == "你好呀"


def test_usage_is_reported_per_request() -> None:
    loop, collector = loop_with(ScriptedGateway(responses=[response("ok")]))
    loop.start(loop_input())

    usage = collector.of(K.MODEL_USAGE)[0]
    assert usage.request_id is not None
    assert getattr(usage.payload, "input_tokens", 0) == 7


# ---- 带工具的轮 ----


def test_a_tool_round_emits_queued_then_a_second_model_call() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("读完了"),
        ]
    )
    loop, collector = loop_with(gateway)

    step = loop.start(loop_input())
    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, ToolRequestAction)
    loop.observe(LoopObservation(content="print(1)", source=ObservationSource.TOOL))

    kinds = collector.kinds()
    assert kinds.count(K.MODEL_STARTED) == 2, "工具回填后要再调一次模型"
    starts = [index for index, kind in enumerate(kinds) if kind is K.MODEL_STARTED]
    # 工具排队发生在第一次模型调用之后, 第二次之前.
    assert starts[0] < kinds.index(K.TOOL_QUEUED) < starts[1]


def test_each_model_call_has_its_own_request_id() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("好"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())
    loop.observe(LoopObservation(content="print(1)", source=ObservationSource.TOOL))

    started = collector.of(K.MODEL_STARTED)
    ids = [event.request_id for event in started]
    assert len(set(ids)) == 2, "两次调用不能共用一个 request_id, 否则终端分不出块"
    indexes = [
        event.payload.call_index
        for event in started
        if isinstance(event.payload, ModelStartedPayload)
    ]
    assert indexes == [1, 2]


def test_tool_queued_carries_the_tool_call_id_and_queue_depth() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"), call("search_text", "c2")))
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())

    queued = collector.of(K.TOOL_QUEUED)[0]
    assert queued.tool_call_id == "c1"
    payload = queued.payload
    assert isinstance(payload, ToolQueuedPayload)
    assert payload.tool_name == "fs_read"
    assert payload.queue_position == 1, "还有一个在排队"


def test_two_tool_calls_are_queued_one_at_a_time() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"), call("search_text", "c2"))),
            response("都读完了"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())
    assert len(collector.of(K.TOOL_QUEUED)) == 1, "一次只派一个"

    loop.observe(LoopObservation(content="print(1)", source=ObservationSource.TOOL))
    assert [event.tool_call_id for event in collector.of(K.TOOL_QUEUED)] == ["c1", "c2"]


def test_the_loop_never_claims_a_tool_finished() -> None:
    """执行结论归协调器. 循环只看到一段回填文本, 用它冒充执行结果会与真相脱节."""
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("好"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())
    loop.observe(LoopObservation(content="print(1)", source=ObservationSource.TOOL))

    for kind in (
        K.TOOL_PREPARED,
        K.POLICY_RESOLVED,
        K.TOOL_STARTED,
        K.TOOL_COMPLETED,
        K.APPROVAL_REQUESTED,
    ):
        assert not collector.of(kind), f"{kind} 不该由循环发布"


# ---- 步骤与终态 ----


def test_step_phases_follow_thinking_tool_thinking_answer() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("好"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())
    loop.observe(LoopObservation(content="print(1)", source=ObservationSource.TOOL))

    phases = [
        event.payload.phase
        for event in collector.of(K.STEP_STARTED)
        if isinstance(event.payload, StepStartedPayload)
    ]
    assert phases == [
        RunPhase.THINKING,
        RunPhase.TOOL,
        RunPhase.THINKING,
        RunPhase.ANSWER,
    ]


def test_turn_completed_summarises_the_round() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("好"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())
    loop.observe(LoopObservation(content="print(1)", source=ObservationSource.TOOL))
    loop.observe(
        LoopObservation(content="answer_delivered", source=ObservationSource.CONTEXT)
    )

    payload = collector.of(K.TURN_COMPLETED)[-1].payload
    assert isinstance(payload, TurnFinishedPayload)
    assert payload.status == "final_answer"
    assert (payload.model_calls, payload.tool_calls) == (2, 1)


def test_a_blocking_stop_ends_the_turn_as_failed() -> None:
    # 模型既不回答也不请求工具: 阻塞停止, 终端要显示失败而不是"完成".
    loop, collector = loop_with(ScriptedGateway(responses=[response("")]))
    loop.start(loop_input())

    assert collector.of(K.TURN_FAILED)
    assert not collector.of(K.TURN_COMPLETED)


def test_model_completed_reports_finish_reason_and_tool_count() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("好"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())

    payload = collector.of(K.MODEL_COMPLETED)[0].payload
    assert isinstance(payload, ModelCompletedPayload)
    assert payload.finish_reason == "tool_calls"
    assert payload.tool_call_count == 1


def test_sequence_is_strictly_increasing_across_the_whole_turn() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("好"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())
    loop.observe(LoopObservation(content="print(1)", source=ObservationSource.TOOL))

    sequences = [event.sequence for event in collector.events]
    assert sequences == list(range(1, len(sequences) + 1))


# ---- 原地打转 ----


def test_the_same_call_with_the_same_arguments_is_blocked_after_two_tries() -> None:
    """回归: fs_list_files 曾被用完全相同的参数连着调上百次.

    每次结果都一样, 模型却读不出该换个做法. 总预算再大也只是让它多转几百圈, 所以拦的
    不是"活干得多", 是"同一件事重复做".
    """
    repeat = response(tool_calls=(call("fs_list_files", "c1"),))
    gateway = ScriptedGateway(
        responses=[
            repeat,
            response(tool_calls=(call("fs_list_files", "c2"),)),
            response(tool_calls=(call("fs_list_files", "c3"),)),
            response("好, 我换个做法"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())
    loop.observe(LoopObservation(content="[]", source=ObservationSource.TOOL))
    loop.observe(LoopObservation(content="[]", source=ObservationSource.TOOL))

    # 第三次被拦下: 只派发了两次, 不会再多.
    assert len(collector.of(K.TOOL_QUEUED)) == 2
    assert gateway.responses == [], "拦截后仍然继续调模型, 而不是卡住"


def test_different_arguments_are_not_treated_as_a_repeat() -> None:
    def _read(path: str, call_id: str) -> ToolCall:
        return ToolCall(
            tool_call_id=call_id,
            name="fs_read",
            arguments=MappingProxyType({"path": path}),
        )

    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_read("a.py", "c1"),)),
            response(tool_calls=(_read("b.py", "c2"),)),
            response(tool_calls=(_read("c.py", "c3"),)),
            response("读完了"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())
    for _ in range(3):
        loop.observe(LoopObservation(content="x", source=ObservationSource.TOOL))
    assert len(collector.of(K.TOOL_QUEUED)) == 3


def test_argument_order_does_not_make_a_call_look_new() -> None:
    def _search(call_id: str, **kwargs: str) -> ToolCall:
        return ToolCall(
            tool_call_id=call_id, name="search_text", arguments=MappingProxyType(kwargs)
        )

    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_search("c1", query="a", path="src"),)),
            response(tool_calls=(_search("c2", path="src", query="a"),)),
            response(tool_calls=(_search("c3", query="a", path="src"),)),
            response("换个做法"),
        ]
    )
    loop, collector = loop_with(gateway)
    loop.start(loop_input())
    loop.observe(LoopObservation(content="[]", source=ObservationSource.TOOL))
    loop.observe(LoopObservation(content="[]", source=ObservationSource.TOOL))
    assert len(collector.of(K.TOOL_QUEUED)) == 2
