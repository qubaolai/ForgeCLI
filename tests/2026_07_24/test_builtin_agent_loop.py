"""BuiltinAgentLoop 单元：单步协议、错误归一、流式增量、取消、事件发布（ADR-0010）。"""

from __future__ import annotations

from typing import Any

import pytest

from forgecli.application.agent_loop import (
    AnswerAction,
    BuiltinAgentLoop,
    ContextPackage,
    LoopDecision,
    LoopEvent,
    LoopEventBus,
    LoopEventKind,
    LoopEventSubscriber,
    LoopInput,
    LoopObservation,
    LoopStop,
    LoopStopReason,
    ModePolicy,
)
from forgecli.application.llm.gateway import (
    CancelToken,
    ChatMessage,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    FinishReason,
    InMemoryModelCatalog,
    ModelAuthError,
    ModelCatalogEntry,
    ModelUsage,
    ProviderCapabilities,
    ProviderRegistry,
    ProviderStreamChunk,
    TextBlock,
)
from forgecli.application.llm.metering import CostEstimator, UsageMeter
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.domain.intents import SessionMode, UserMessage
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")
_STREAMING = ProviderCapabilities(supports_streaming=True)


def _catalog() -> InMemoryModelCatalog:
    return InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek",
                model="deepseek-chat",
                context_window=65536,
                input_price_per_1k=0.2,
                output_price_per_1k=0.4,
            ),
        )
    )


def _gateway(provider: FakeModelProvider) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    return DefaultLlmGateway(
        registry, resolver=DefaultModelSelectionResolver(_catalog(), current_model=_REF)
    )


def _loop(provider: FakeModelProvider, **kwargs: Any) -> BuiltinAgentLoop:
    meter = UsageMeter(CostEstimator(_catalog()), clock=lambda: "t0")
    return BuiltinAgentLoop(
        _gateway(provider),
        meter,
        request_id_factory=lambda: "req_fixed",
        **kwargs,
    )


def _input(text: str = "你好") -> LoopInput:
    mode = SessionMode.ACCEPT_EDITS
    return LoopInput(
        turn_id="turn_0001",
        session_id="sess_1",
        user_intent=UserMessage(raw_text=text),
        mode=mode,
        mode_policy=ModePolicy(mode=mode),
        context_package=ContextPackage(
            messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock(text),)),)
        ),
    )


def _observe_answer(loop: BuiltinAgentLoop) -> LoopStop:
    result = loop.observe(
        LoopObservation(content="answer_delivered", source="turn_service")
    )
    assert isinstance(result, LoopStop)
    return result


def test_complete_path_three_step_protocol_and_usage_draft() -> None:
    provider = FakeModelProvider(
        content="答",
        usage=ModelUsage(input_tokens=1000, output_tokens=500, total_tokens=1500),
    )
    loop = _loop(provider)

    step = loop.start(_input())

    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, AnswerAction)
    assert step.next_action.text == "答"
    stop = _observe_answer(loop)
    assert stop.reason is LoopStopReason.FINAL_ANSWER
    assert stop.resumable is False

    assert len(loop.usage_drafts) == 1
    draft = loop.usage_drafts[0]
    assert draft.request_id == "req_fixed"
    assert (draft.provider, draft.model) == ("deepseek", "deepseek-chat")
    assert draft.estimated is False
    assert draft.estimated_cost == 1000 * 0.2 / 1000 + 500 * 0.4 / 1000


def test_gateway_error_maps_to_blocking_stop_with_actionable_message() -> None:
    loop = _loop(FakeModelProvider(error=ModelAuthError("401 unauthorized")))

    step = loop.start(_input())

    assert isinstance(step, LoopStop)
    assert step.reason is LoopStopReason.MODEL_ERROR_BLOCKING
    assert step.message is not None and "认证失败" in step.message
    assert loop.usage_drafts == ()


def test_pre_cancelled_token_stops_before_provider_called() -> None:
    token = CancelToken()
    token.cancel()
    provider = FakeModelProvider(content="不该被调用")
    loop = _loop(provider, cancel_token_factory=lambda: token)

    step = loop.start(_input())

    assert isinstance(step, LoopStop)
    assert step.reason is LoopStopReason.USER_CANCELLED
    assert provider.complete_calls == 0


def test_streaming_deltas_flow_to_sink_and_draft_carries_model_identity() -> None:
    provider = FakeModelProvider(
        capabilities=_STREAMING,
        stream_chunks=(
            ProviderStreamChunk(delta_text="你"),
            ProviderStreamChunk(delta_text="好"),
            ProviderStreamChunk(
                usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
                finish_reason=FinishReason.STOP,
            ),
        ),
    )
    deltas: list[str] = []
    loop = _loop(provider, on_delta=deltas.append)

    step = loop.start(_input())

    assert deltas == ["你", "好"]
    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, AnswerAction)
    assert step.next_action.text == "你好"
    assert len(loop.usage_drafts) == 1
    draft = loop.usage_drafts[0]
    # 流式收尾块带 provider/model（ADR-0011 §9 完整汇总），草稿据此计价。
    assert (draft.provider, draft.model) == ("deepseek", "deepseek-chat")
    assert draft.usage.total_tokens == 5
    assert draft.estimated is False


def test_sink_with_non_streaming_provider_falls_back_to_complete() -> None:
    provider = FakeModelProvider(content="ok")  # 默认能力：不支持流式
    deltas: list[str] = []
    loop = _loop(provider, on_delta=deltas.append)

    step = loop.start(_input())

    assert isinstance(step, LoopDecision)
    assert isinstance(step.next_action, AnswerAction)
    assert step.next_action.text == "ok"
    assert deltas == []
    assert provider.complete_calls == 1


def test_cancel_mid_stream_keeps_partial_answer_and_estimated_draft() -> None:
    token = CancelToken()
    provider = FakeModelProvider(
        capabilities=_STREAMING,
        stream_chunks=(
            ProviderStreamChunk(delta_text="早"),
            ProviderStreamChunk(delta_text="晚"),
            ProviderStreamChunk(finish_reason=FinishReason.STOP),
        ),
    )

    def cancel_after_first(delta: str) -> None:
        token.cancel()

    loop = _loop(
        provider, on_delta=cancel_after_first, cancel_token_factory=lambda: token
    )

    step = loop.start(_input())

    assert isinstance(step, LoopStop)
    assert step.reason is LoopStopReason.USER_CANCELLED
    assert loop.partial_answer == "早"
    assert len(loop.usage_drafts) == 1
    assert loop.usage_drafts[0].estimated is True


def test_start_twice_and_premature_observe_raise() -> None:
    loop = _loop(FakeModelProvider(content="答"))
    loop.start(_input())
    with pytest.raises(RuntimeError, match="只能 start 一次"):
        loop.start(_input())

    fresh = _loop(FakeModelProvider(content="答"))
    with pytest.raises(RuntimeError, match="未 start"):
        fresh.observe(LoopObservation(content="noop"))


class _KindRecorder(LoopEventSubscriber):
    def __init__(self) -> None:
        self.kinds: list[LoopEventKind] = []

    def on_event(self, event: LoopEvent) -> None:
        self.kinds.append(event.kind)


def test_events_published_in_order_over_full_turn() -> None:
    bus = LoopEventBus()
    recorder = _KindRecorder()
    bus.subscribe(recorder)
    loop = _loop(FakeModelProvider(content="答"), event_bus=bus)

    loop.start(_input())
    _observe_answer(loop)

    assert recorder.kinds == [
        LoopEventKind.LOOP_STARTED,
        LoopEventKind.MODEL_REQUESTED,
        LoopEventKind.MODEL_COMPLETED,
        LoopEventKind.ACTION_REQUESTED,
        LoopEventKind.LOOP_STOPPED,
    ]
    assert bus.isolated_failures == ()
