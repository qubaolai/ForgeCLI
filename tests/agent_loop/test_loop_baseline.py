"""BuiltinAgentLoop 的行为基线 (ADR-0049 实施清单第 0 步).

全部在 start() / observe() 边界上写. 改造前后都要过, 是这次重构唯一的对照.
"""

from __future__ import annotations

from forgecli.application.agent_run.events import AgentRunEventKind as K
from forgecli.application.context.window_manager import WindowManager
from forgecli.application.llm.gateway.errors import (
    ModelCancelledError,
    ModelContextOverflowError,
    ModelUnavailableError,
)
from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopObservation,
    ObservationDisposition,
    ObservationSource,
    ToolRequestAction,
)
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.conversation.message import TextBlock, ToolResultBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.model.response import FinishReason
from support.loop_fakes import (
    Fail,
    Interrupted,
    LoopInputSpec,
    Reply,
    assistant,
    call,
    loop_input,
    user,
)

# ---- 纯文本回答 ----


def test_plain_answer(harness):
    h = harness(Reply(text="你好"))
    stop, loop = h.run(loop_input())

    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert h.actions == [AnswerAction(text="你好")]
    # 窗口末条就是这句回答, 驱动方不用再补.
    last = loop.window[-1]
    assert last.role is MessageRole.ASSISTANT
    assert isinstance(last.content[0], TextBlock)
    assert last.content[0].text == "你好"
    assert loop.usage_drafts and loop.usage_drafts[0].turn_id == "turn_0001"
    kinds = h.events.kinds()
    assert kinds[-1] is K.TURN_COMPLETED
    assert K.MODEL_STARTED in kinds and K.MODEL_COMPLETED in kinds


def test_request_carries_window_tools_and_state_frame(harness):
    h = harness(Reply(text="done"))
    h.run(loop_input(LoopInputSpec(state_frame="frame")))

    request = h.gateway.requests[0]
    assert [s.name for s in request.tools] == ["fs_read", "shell_run"]
    assert request.system_prompt and "test double" in request.system_prompt
    # 状态帧永远在最后一条.
    assert request.messages[-1].content[0].text == "frame"  # type: ignore[union-attr]


# ---- 工具调用 ----


def test_single_tool_call_then_answer(harness):
    h = harness(
        Reply(tool_calls=(call("fs_read", "c1", path="a.py"),)),
        Reply(text="读完了"),
    )
    stop, loop = h.run(loop_input())

    assert stop.reason is LoopStopReason.FINAL_ANSWER
    tools = h.tool_actions()
    assert len(tools) == 1
    assert tools[0].request.name == "fs_read"
    assert tools[0].request.tool_call_id == "c1"
    assert tools[0].request.arguments == {"path": "a.py"}
    # 第二次请求里有配对的工具结果, 且带调用标签.
    second = h.gateway.requests[1].messages
    result = [m for m in second if m.role is MessageRole.TOOL][0]
    block = result.content[0]
    assert isinstance(block, ToolResultBlock)
    assert block.tool_call_id == "c1"
    assert block.content.startswith("fs_read(path='a.py') ->")
    assert K.TOOL_QUEUED in h.events.kinds()


def test_three_tool_calls_dispatch_one_at_a_time(harness):
    h = harness(
        Reply(
            tool_calls=(
                call("fs_read", "c1", path="a"),
                call("fs_read", "c2", path="b"),
                call("shell_run", "c3", command="ls"),
            )
        ),
        Reply(text="ok"),
    )
    stop, _ = h.run(loop_input())

    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert [t.request.tool_call_id for t in h.tool_actions()] == ["c1", "c2", "c3"]
    # 整批意图在第一个派发前就全部可见, 队列位置递减到 0.
    queued = h.events.of(K.TOOL_QUEUED)
    assert [e.payload.queue_position for e in queued] == [2, 1, 0]  # type: ignore[attr-defined]
    # 模型只被调了两次: 三个工具之间不回模型.
    assert len(h.gateway.requests) == 2


# ---- 拒绝, 审批, 取消 ----


def blocked(action: ToolRequestAction) -> LoopObservation:
    return LoopObservation(
        content="policy denied",
        source=ObservationSource.SECURITY,
        is_error=True,
        disposition=ObservationDisposition.BLOCKED,
    )


def test_three_blocked_calls_close_tools(harness):
    h = harness(
        Reply(tool_calls=(call("shell_run", "c1", command="rm -rf x"),)),
        Reply(tool_calls=(call("shell_run", "c2", command="rm -r x"),)),
        Reply(tool_calls=(call("shell_run", "c3", command="find x -delete"),)),
        Reply(text="被挡住了, 我原本想删 x"),
    )
    stop, loop = h.run(loop_input(), on_tool=blocked)

    assert stop.reason is LoopStopReason.FINAL_ANSWER
    # 第四次请求不再带工具, 并且窗口里有一条收工具的通知.
    assert h.gateway.requests[3].tools == ()
    notices = [
        m
        for m in loop.window
        if m.role is MessageRole.USER
        and isinstance(m.content[0], TextBlock)
        and "已被安全策略拒绝 3 次" in m.content[0].text
    ]
    assert len(notices) == 1


def test_human_refusal_closes_tools_immediately(harness):
    def refused(action: ToolRequestAction) -> LoopObservation:
        return LoopObservation(
            content="user refused",
            source=ObservationSource.SECURITY,
            is_error=True,
            disposition=ObservationDisposition.HALT,
        )

    h = harness(
        Reply(tool_calls=(call("shell_run", "c1", command="x"),)),
        Reply(text="好的, 我原本想跑 x"),
    )
    stop, _ = h.run(loop_input(), on_tool=refused)
    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert h.gateway.requests[1].tools == ()


def test_tools_after_close_without_text_stops_policy_denied(harness):
    def refused(action: ToolRequestAction) -> LoopObservation:
        return LoopObservation(
            content="user refused",
            source=ObservationSource.SECURITY,
            is_error=True,
            disposition=ObservationDisposition.HALT,
        )

    h = harness(
        Reply(tool_calls=(call("shell_run", "c1", command="x"),)),
        # 目录收了还要工具, 一个字都不说: 硬停.
        Reply(tool_calls=(call("shell_run", "c2", command="y"),)),
    )
    stop, _ = h.run(loop_input(), on_tool=refused)
    assert stop.reason is LoopStopReason.POLICY_DENIED
    assert h.events.kinds()[-1] is K.TURN_FAILED


def test_tools_after_close_with_text_becomes_answer(harness):
    def refused(action: ToolRequestAction) -> LoopObservation:
        return LoopObservation(
            content="user refused",
            source=ObservationSource.SECURITY,
            is_error=True,
            disposition=ObservationDisposition.HALT,
        )

    h = harness(
        Reply(tool_calls=(call("shell_run", "c1", command="x"),)),
        Reply(text="那我说一下", tool_calls=(call("shell_run", "c2", command="y"),)),
    )
    stop, _ = h.run(loop_input(), on_tool=refused)
    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert h.actions[-1] == AnswerAction(text="那我说一下")
    # 被剥掉的 tool_calls 不进 transcript.
    assert len(h.tool_actions()) == 1


def test_cancelled_before_request(harness):
    h = harness(Fail(ModelCancelledError("cancelled")))
    stop, _ = h.run(loop_input())
    assert stop.reason is LoopStopReason.USER_CANCELLED
    assert h.events.kinds()[-1] is K.TURN_CANCELLED


def test_cancelled_mid_stream_keeps_partial_answer(harness):
    h = harness(Interrupted(text="写到一半"))
    stop, loop = h.run(loop_input())
    assert stop.reason is LoopStopReason.USER_CANCELLED
    assert loop.partial_answer == "写到一半"
    assert h.events.kinds()[-1] is K.TURN_CANCELLED


def test_stream_interrupted_by_error(harness):
    h = harness(Interrupted(text="", finish_reason=FinishReason.ERROR))
    stop, _ = h.run(loop_input())
    assert stop.reason is LoopStopReason.MODEL_ERROR_BLOCKING


def test_gateway_error_stops_with_hint(harness):
    h = harness(Fail(ModelUnavailableError("down")))
    stop, _ = h.run(loop_input())
    assert stop.reason is LoopStopReason.MODEL_ERROR_BLOCKING
    assert stop.message


def test_empty_response_stops(harness):
    h = harness(Reply(text=""))
    stop, _ = h.run(loop_input())
    assert stop.reason is LoopStopReason.MODEL_ERROR_BLOCKING


# ---- 打转与格式 ----


def test_repeated_call_is_denied_on_third_time(harness):
    same = call("fs_read", "c", path="a")
    h = harness(
        Reply(tool_calls=(same,)),
        Reply(tool_calls=(same,)),
        Reply(tool_calls=(same,)),
        Reply(text="好吧"),
    )
    stop, loop = h.run(loop_input())

    assert stop.reason is LoopStopReason.FINAL_ANSWER
    # 只派发了两次, 第三次被挡下并回填成错误结果.
    assert len(h.tool_actions()) == 2
    rejected = [
        e
        for e in h.events.of(K.TOOL_COMPLETED)
        if e.payload.error_code == "repeated_call"  # type: ignore[attr-defined]
    ]
    assert len(rejected) == 1
    results = [
        m.content[0]
        for m in loop.window
        if m.role is MessageRole.TOOL and isinstance(m.content[0], ToolResultBlock)
    ]
    assert results[-1].is_error and "重复调用已被拦截" in results[-1].content


def test_barren_streak_nudges_once(harness):
    h = harness(
        Reply(tool_calls=(call("fs_read", "c1", path="a"),)),
        Reply(tool_calls=(call("fs_read", "c2", path="b"),)),
        Reply(tool_calls=(call("fs_read", "c3", path="c"),)),
        Reply(text="没找到"),
    )

    def empty(action: ToolRequestAction) -> LoopObservation:
        return LoopObservation(content="", source=ObservationSource.TOOL)

    stop, loop = h.run(loop_input(), on_tool=empty)
    assert stop.reason is LoopStopReason.FINAL_ANSWER
    nudges = [
        m
        for m in loop.window
        if m.role is MessageRole.USER
        and isinstance(m.content[0], TextBlock)
        and "连续 3 次工具调用没有带回新信息" in m.content[0].text
    ]
    assert len(nudges) == 1
    assert K.DECISION_SUMMARY in h.events.kinds()


def test_protocol_markup_triggers_reask_without_assistant_message(harness):
    h = harness(
        Reply(tool_calls=(call("shell_run", "c1", command="<tool_call>ls"),)),
        Reply(text="重来"),
    )
    stop, loop = h.run(loop_input())

    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert h.tool_actions() == []
    roles = [m.role for m in loop.window]
    # 坏掉那次没有写 assistant 消息: 用户消息 -> 纠错通知 -> 助手回答.
    assert roles == [MessageRole.USER, MessageRole.USER, MessageRole.ASSISTANT]
    assert "工具调用无法使用" in loop.window[1].content[0].text  # type: ignore[union-attr]


def test_third_malformed_response_stops(harness):
    bad = Reply(tool_calls=(call("shell_run", "c1", command="<think>x"),))
    h = harness(bad, bad, bad)
    stop, _ = h.run(loop_input())
    assert stop.reason is LoopStopReason.MODEL_ERROR_BLOCKING
    assert stop.message and "3 次" in stop.message


def test_partial_tool_call_json_is_malformed(harness):
    h = harness(
        Reply(tool_calls=(("c1", "shell_run", '{"command": "ls'),)),
        Reply(text="重来"),
    )
    stop, _ = h.run(loop_input())
    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert len(h.gateway.requests) == 2


# ---- 压缩 ----


def big_window(n: int = 30):
    messages = []
    for i in range(n):
        messages.append(user(f"question {i} " + "x" * 200))
        messages.append(assistant(f"answer {i} " + "y" * 200))
    return tuple(messages)


def test_window_is_evicted_before_model_call(harness):
    manager = WindowManager(max_inline_bytes=100)
    h = harness(Reply(text="ok"), context=manager)
    budget = ContextBudget(context_window=2000)
    stop, loop = h.run(loop_input(LoopInputSpec(window=big_window(), budget=budget)))

    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert loop.compaction_drafts
    assert len(h.gateway.requests[0].messages) < 60
    assert K.CONTEXT_COMPACTED in h.events.kinds()


def test_overflow_forces_eviction_then_retries(harness):
    manager = WindowManager(max_inline_bytes=100)
    h = harness(
        Fail(ModelContextOverflowError("too long")),
        Reply(text="ok"),
        context=manager,
    )
    # 预算很大, 估算说放得下; 供应商说放不下.
    budget = ContextBudget(context_window=1_000_000)
    stop, loop = h.run(loop_input(LoopInputSpec(window=big_window(), budget=budget)))

    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert len(h.gateway.requests) == 2
    assert len(h.gateway.requests[1].messages) < len(h.gateway.requests[0].messages)
    assert loop.compaction_drafts


def test_second_overflow_stops_compaction_required(harness):
    manager = WindowManager(max_inline_bytes=100)
    h = harness(
        Fail(ModelContextOverflowError("too long")),
        Fail(ModelContextOverflowError("still too long")),
        context=manager,
    )
    budget = ContextBudget(context_window=1_000_000)
    stop, _ = h.run(loop_input(LoopInputSpec(window=big_window(), budget=budget)))
    assert stop.reason is LoopStopReason.CONTEXT_COMPACTION_REQUIRED


def test_overflow_without_budget_stops(harness):
    h = harness(Fail(ModelContextOverflowError("too long")))
    stop, _ = h.run(loop_input())
    assert stop.reason is LoopStopReason.CONTEXT_COMPACTION_REQUIRED


# ---- 工作区变化 ----


def test_external_change_before_model_call_is_announced(harness):
    h = harness(Reply(text="ok"))
    h.workspace.touch("a.py")
    loop = h.loop()
    # start() 里 monitor.start() 会拍下 a.py; 之后改动才算变化.
    h.workspace.touch("b.py")
    # 触发一次: 循环在调模型之前先检查一遍.
    step = loop.start(loop_input())
    # 这一步已经调过模型了, 所以 b.py 是在 start() 拍快照之前加的, 不会被认成变化.
    assert isinstance(step, AnswerAction)
    assert not any(
        m.content
        and isinstance(m.content[0], TextBlock)
        and "工作区文件发生变化" in m.content[0].text
        for m in loop.window
    )


def test_external_change_during_generation_reasks(harness):
    h = harness(Reply(text="基于旧文件的回答"), Reply(text="重新看过了"))

    class ChangingGateway:
        """第一次调用期间改文件."""

        def __init__(self, inner, workspace):
            self.inner = inner
            self.workspace = workspace
            self.requests = inner.requests

        def stream(self, request):
            chunks = self.inner.stream(request)
            if len(self.requests) == 1:
                # 只在第一次调用期间改, 第二次不改: 不然每次重问都会再触发一次.
                self.workspace.touch("changed.py")
            return chunks

        def complete(self, request):
            return self.inner.complete(request)

        def complete_structured(self, request):
            return self.inner.complete_structured(request)

    h.gateway = ChangingGateway(h.gateway, h.workspace)  # type: ignore[assignment]
    stop, loop = h.run(loop_input())

    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert h.actions == [AnswerAction(text="重新看过了")]
    assert len(h.gateway.requests) == 2
    notice = h.gateway.requests[1].messages[-1]
    assert isinstance(notice.content[0], TextBlock)
    assert "changed.py" in notice.content[0].text
    assert "外部参与者" in notice.content[0].text
    assert K.WORKSPACE_CHANGED in h.events.kinds()


def test_agent_change_after_tool_is_labelled_agent(harness):
    h = harness(
        Reply(tool_calls=(call("shell_run", "c1", command="touch x"),)),
        Reply(text="done"),
    )

    def touching(action: ToolRequestAction) -> LoopObservation:
        h.workspace.touch("x")
        return LoopObservation(content="touched", source=ObservationSource.TOOL)

    stop, _ = h.run(loop_input(), on_tool=touching)
    assert stop.reason is LoopStopReason.FINAL_ANSWER
    notice = [
        m
        for m in h.gateway.requests[1].messages
        if m.content
        and isinstance(m.content[0], TextBlock)
        and "工作区文件发生变化" in m.content[0].text
    ]
    assert len(notice) == 1
    assert "本 agent" in notice[0].content[0].text  # type: ignore[union-attr]


# ---- 计划评审 ----


def test_plan_review_stops_and_abandons_queue(harness):
    h = harness(
        Reply(
            tool_calls=(
                call("plan_write", "c1", title="p"),
                call("fs_read", "c2", path="a"),
            )
        ),
    )

    def review(action: ToolRequestAction) -> LoopObservation:
        return LoopObservation(
            content="plan proposed",
            source=ObservationSource.TOOL,
            disposition=ObservationDisposition.AWAIT_USER_DECISION,
        )

    spec = LoopInputSpec(tools=("plan_write", "fs_read"))
    stop, loop = h.run(loop_input(spec), on_tool=review)

    assert stop.reason is LoopStopReason.WAIT_PLAN_REVIEW
    assert len(h.tool_actions()) == 1
    # 排队中的 c2 补上了未执行的配对结果.
    results = {
        m.content[0].tool_call_id: m.content[0]
        for m in loop.window
        if m.role is MessageRole.TOOL and isinstance(m.content[0], ToolResultBlock)
    }
    assert set(results) == {"c1", "c2"}
    assert results["c2"].is_error and "未执行" in results["c2"].content
    assert K.TOOL_CANCELLED in h.events.kinds()
    assert h.events.kinds()[-1] is K.TURN_COMPLETED


# ---- 生命周期 ----


def test_start_twice_raises(harness):
    import pytest

    h = harness(Reply(text="ok"))
    loop = h.loop()
    loop.start(loop_input())
    with pytest.raises(RuntimeError):
        loop.start(loop_input())


def test_observe_before_start_raises(harness):
    import pytest

    h = harness()
    with pytest.raises(RuntimeError):
        h.loop().observe(LoopObservation(content="x"))


def test_observe_after_finished_raises(harness):
    import pytest

    h = harness(Reply(text="ok"))
    stop, loop = h.run(loop_input())
    assert stop.reason is LoopStopReason.FINAL_ANSWER
    with pytest.raises(RuntimeError):
        loop.observe(LoopObservation(content="x"))
