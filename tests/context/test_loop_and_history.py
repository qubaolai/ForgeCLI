"""循环与跨回合历史 (ADR-0032 决策 1 / 7 / 8).

三件事在这里合测, 因为它们是同一条链上前后相接的三段: 循环调用整理器, 整理不动就停,
而一轮结束之后留给下一轮的只有结论行与摘要.
"""

from __future__ import annotations

from forgecli.application.agent_turn.agent_turn_service import _rebuild_transcript
from forgecli.application.context.manager import ContextManager
from forgecli.application.llm.catalog import InMemoryModelCatalog
from forgecli.application.llm.gateway.errors import ModelContextOverflowError
from forgecli.application.llm.metering import CostEstimator, UsageMeter
from forgecli.application.tool_request.observations import (
    ObservationKind,
    ToolObservation,
)
from forgecli.domain.agent.actions import AnswerAction
from forgecli.domain.agent.run_events import AgentRunEventKind
from forgecli.domain.agent.state import ContextPackage, LoopInput
from forgecli.domain.agent.stop import LoopStopReason, StopClassification
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.intents import SessionMode
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.request import ModelRequest
from forgecli.domain.model.response import FinishReason, ModelResponse, ModelUsage
from forgecli.domain.session.events import EventType, SessionEvent
from forgecli.domain.tool.result import (
    ContentPart,
    ResultProvenance,
    ToolResult,
    ToolResultStatus,
)
from support.fakes import MemoryArtifactStore, prompt
from support.loop_harness import (
    Collector,
    ScriptedGateway,
    loop_with,
    response,
)

_BIG = "y" * 8000


def _meter() -> UsageMeter:
    return UsageMeter(CostEstimator(InMemoryModelCatalog()))


def _long_history() -> tuple[ChatMessage, ...]:
    """长到必须走二级摘要, 且切点落在配对之外."""
    messages: list[ChatMessage] = []
    for index in range(8):
        messages.append(
            ChatMessage(role=MessageRole.USER, content=(TextBlock(f"问题 {index}"),))
        )
        messages.append(
            ChatMessage(role=MessageRole.ASSISTANT, content=(TextBlock(_BIG),))
        )
    return tuple(messages)


class _SummaryGateway(ScriptedGateway):
    """只写摘要的替身. 它与循环用的那个 gateway 是两个: 循环走脚本, 摘要走这里."""

    def complete(self, request: ModelRequest) -> ModelResponse:  # type: ignore[override]
        return ModelResponse(
            request_id=request.request_id,
            provider="fake",
            model="fake-model",
            content="前面读了几个文件.",
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=4321, output_tokens=120),
            latency_ms=9.0,
        )


class _RejectingGateway(ScriptedGateway):
    """前 ``rejections`` 次流式调用被供应商以"提示词太长"拒掉.

    在生成器里抛而不是在 stream() 入口抛: 真实适配器是惰性的, HTTP 400 发生在拉第一
    个块的时候 —— 循环里那个 `except ModelBadRequestError` 就是这样被绕过去的.
    """

    rejections: int = 0

    def stream(self, request: ModelRequest):  # type: ignore[no-untyped-def,override]
        if self.rejections:
            self.rejections -= 1
            self.requests.append(request)

            def _rejected():  # type: ignore[no-untyped-def]
                raise ModelContextOverflowError("provider 拒绝请求（400）: 提示词太长")
                yield

            return _rejected()
        return super().stream(request)


def _overflow_loop(rejections: int) -> tuple[object, Collector, _RejectingGateway]:
    gateway = _RejectingGateway(responses=[response("好的")])
    gateway.rejections = rejections
    loop, collector = loop_with(
        gateway,
        context=ContextManager(
            artifacts=MemoryArtifactStore(),
            gateway=_SummaryGateway(),
            meter=_meter(),
        ),
    )
    return loop, collector, gateway


def _overflow_input() -> LoopInput:
    return LoopInput(
        turn_id="turn-1",
        session_id="sess-1",
        mode=SessionMode.ACCEPT_EDITS,
        context_package=ContextPackage(
            prompt=prompt(),
            messages=_long_history(),
            # 预算宽到不会自己触发压缩: 这里要测的正是"估算说放得下, 供应商说放不下".
            budget=ContextBudget(context_window=10_000_000),
        ),
    )


def test_a_provider_side_overflow_compacts_and_retries() -> None:
    """近似估算与供应商的分词永远有偏差, 所以这条路必须存在.

    以前它按 MODEL_ERROR_BLOCKING 处理: 一轮跑了几十步的工作, 连同已经花掉的 token,
    在最后一次调用上整个作废.
    """
    loop, collector, gateway = _overflow_loop(rejections=1)

    step = loop.start(_overflow_input())

    assert isinstance(step.next_action, AnswerAction)
    assert len(gateway.requests) == 2, "压完之后要把这一步重发一次"
    assert collector.of(AgentRunEventKind.CONTEXT_COMPACTED)
    assert len(gateway.requests[1].messages) < len(gateway.requests[0].messages)


def test_a_second_overflow_pauses_instead_of_killing_the_turn() -> None:
    """摘要跑完只剩一段摘要加最近几条, 再超就不是压缩能解决的问题了.

    停在 RESUMABLE_PAUSE 而不是 MODEL_ERROR_BLOCKING: 这一轮干不下去, 但会话没坏.
    """
    loop, _, gateway = _overflow_loop(rejections=2)

    step = loop.start(_overflow_input())

    assert step.reason is LoopStopReason.CONTEXT_COMPACTION_REQUIRED
    assert step.reason.classification is StopClassification.RESUMABLE_PAUSE
    assert len(gateway.requests) == 2, "只强压一次, 不反复撞墙"


def _event(event_type: EventType, **payload: object) -> SessionEvent:
    return SessionEvent(
        event_id=f"evt_{event_type.value}",
        session_id="s1",
        type=event_type,
        created_at="2026-08-25T00:00:00Z",
        payload=payload,
    )


# ---- 决策 1: 循环在调模型之前整理一次 ----


def test_a_context_that_still_does_not_fit_stops_the_turn_instead_of_being_sent() -> (
    None
):
    """网关那条溢出错误是终止性的 —— 发出去等于让这一轮的全部工作作废."""
    gateway = ScriptedGateway(responses=[response("好的")])
    loop, _ = loop_with(
        gateway, context=ContextManager(artifacts=MemoryArtifactStore())
    )

    step = loop.start(
        LoopInput(
            turn_id="turn-1",
            session_id="sess-1",
            mode=SessionMode.ACCEPT_EDITS,
            context_package=ContextPackage(
                prompt=prompt(),
                messages=(
                    ChatMessage(
                        role=MessageRole.USER, content=(TextBlock("x" * 40000),)
                    ),
                ),
                # 一个再怎么压也放不下的窗口.
                budget=ContextBudget(context_window=100),
            ),
        )
    )

    assert step.reason is LoopStopReason.CONTEXT_COMPACTION_REQUIRED
    assert step.reason.classification is StopClassification.RESUMABLE_PAUSE
    assert gateway.requests == [], "压不下去就不该把请求发出去"


def test_without_a_budget_the_loop_behaves_exactly_as_before() -> None:
    """缺预算只是不压缩. 猜一个窗口比不压更糟 —— 猜小了平白压掉内容."""
    gateway = ScriptedGateway(responses=[response("好的")])
    loop, _ = loop_with(
        gateway, context=ContextManager(artifacts=MemoryArtifactStore())
    )

    loop.start(
        LoopInput(
            turn_id="turn-1",
            session_id="sess-1",
            mode=SessionMode.ACCEPT_EDITS,
            context_package=ContextPackage(
                prompt=prompt(),
                messages=(
                    ChatMessage(
                        role=MessageRole.USER, content=(TextBlock("x" * 40000),)
                    ),
                ),
            ),
        )
    )

    assert len(gateway.requests) == 1
    assert loop.compaction_drafts == ()


# ---- 决策 7: 跨回合只留结论行 ----


def test_a_tool_call_leaves_one_line_not_its_output() -> None:
    observation = ToolObservation(
        kind=ObservationKind.TOOL_RESULT,
        message="",
        invocation_id="c1",
        tool_name="fs_read",
        result=ToolResult(
            invocation_id="c1",
            tool_name="fs_read",
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text="第一行\n第二行\n第三行"),),
            provenance=ResultProvenance(artifact_id="0123456789abcdef"),
        ),
    )

    line = observation.digest_line()

    assert line == "第一行 (0123456789abcdef)"
    assert "第二行" not in line


def test_a_failed_call_keeps_its_status_in_the_line() -> None:
    observation = ToolObservation(
        kind=ObservationKind.POLICY_DENIED,
        message="不许",
        invocation_id="c1",
        tool_name="shell_run",
    )

    assert observation.digest_line() == "policy_denied"


# ---- 决策 8: resume 从最后一次摘要接上 ----


def test_resume_replays_from_the_last_summary() -> None:
    events = [
        _event(EventType.USER_MESSAGE, text="第一个问题"),
        _event(EventType.ASSISTANT_MESSAGE, text="第一个回答"),
        _event(
            EventType.CONTEXT_COMPACTED,
            level="summary",
            summary="早前那一段已经解决.",
        ),
        _event(EventType.USER_MESSAGE, text="第二个问题"),
        _event(EventType.ASSISTANT_MESSAGE, text="第二个回答"),
    ]

    transcript = _rebuild_transcript(events)

    texts = [
        block.text
        for message in transcript
        for block in message.content
        if isinstance(block, TextBlock)
    ]
    joined = " ".join(texts)
    assert "第一个问题" not in joined, "压缩点之前的原文不再重放"
    assert "第一个回答" not in joined
    assert any("已经解决" in text for text in texts)
    assert texts[-1] == "第二个回答"


def test_a_downgrade_event_does_not_truncate_the_history() -> None:
    """降级改的是回合内工具结果的正文, 而工具结果本来就不跨回合."""
    events = [
        _event(EventType.USER_MESSAGE, text="第一个问题"),
        _event(EventType.CONTEXT_COMPACTED, level="downgrade", summary=""),
        _event(EventType.ASSISTANT_MESSAGE, text="第一个回答"),
    ]

    transcript = _rebuild_transcript(events)

    texts = [
        block.text
        for message in transcript
        for block in message.content
        if isinstance(block, TextBlock)
    ]
    assert texts == ["第一个问题", "第一个回答"]


# ---- ADR-0037: 压缩的账与压缩这件事都要出得来 ----


def test_the_loop_carries_the_compaction_bill_out_with_the_other_usage() -> None:
    """压缩那次调用的草稿混进 usage_drafts, 走与模型调用完全相同的落盘路径.

    分开一条路的话, AgentTurnService 就要认识"还有另一种 usage", 而它已经有一条
    `for draft in outcome.usage_drafts` 了 —— 两条路迟早有一条漏掉.
    """
    gateway = ScriptedGateway(responses=[response(text="好了")])
    loop, collector = loop_with(
        gateway,
        context=ContextManager(
            artifacts=MemoryArtifactStore(), gateway=_SummaryGateway(), meter=_meter()
        ),
    )

    loop.start(
        LoopInput(
            turn_id="t1",
            session_id="s1",
            mode=SessionMode.ACCEPT_EDITS,
            context_package=ContextPackage(
                prompt=prompt(),
                messages=_long_history(),
                budget=ContextBudget(context_window=4000),
            ),
        )
    )

    origins = [draft.origin for draft in loop.usage_drafts]
    assert RequestOrigin.COMPACT in origins, "压缩的账没跟出来"


def test_compaction_is_visible_in_the_run_stream() -> None:
    """以前它在界面上完全不可见: 用户看到的是"卡了几秒".

    两类事件都要发 —— 压缩本身 (省下多少) 与它的用量 (花掉多少) 是方向相反的两个数,
    只报一个都会被读错.
    """
    gateway = ScriptedGateway(responses=[response(text="好了")])
    loop, collector = loop_with(
        gateway,
        context=ContextManager(
            artifacts=MemoryArtifactStore(), gateway=_SummaryGateway(), meter=_meter()
        ),
    )

    loop.start(
        LoopInput(
            turn_id="t1",
            session_id="s1",
            mode=SessionMode.ACCEPT_EDITS,
            context_package=ContextPackage(
                prompt=prompt(),
                messages=_long_history(),
                budget=ContextBudget(context_window=4000),
            ),
        )
    )

    kinds = collector.kinds()
    assert AgentRunEventKind.CONTEXT_COMPACTED in kinds
    compact_usage = [
        event
        for event in collector.of(AgentRunEventKind.MODEL_USAGE)
        if getattr(event.payload, "origin", "") == RequestOrigin.COMPACT.value
    ]
    assert compact_usage, "压缩的用量事件没发出去, 前端合计里就少这一块"
