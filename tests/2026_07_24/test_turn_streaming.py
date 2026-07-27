"""全 turn 流式路径：增量外送、取消轮落盘语义、取消轮进历史。

对应 ADR-0010（loop 驱动）与 ADR-0011 §9（流式收尾 / 中断）。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.agent_loop import BuiltinAgentLoop
from forgecli.application.agent_turn import AgentTurnService, TurnCancelSource
from forgecli.application.llm.gateway import (
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    FinishReason,
    InMemoryModelCatalog,
    ModelCatalogEntry,
    ModelUsage,
    ProviderCapabilities,
    ProviderRegistry,
    ProviderStreamChunk,
)
from forgecli.application.llm.metering import CostEstimator, UsageMeter
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.session import EventType, SessionEvent, SessionService
from forgecli.domain.conversation import TurnStatus
from forgecli.infrastructure.llm.adapters import FakeModelProvider
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore

_SID = "sid"
_REF = ModelRef(provider="deepseek", model="deepseek-chat")
_STREAMING = ProviderCapabilities(supports_streaming=True)
_CHUNKS = (
    ProviderStreamChunk(delta_text="早"),
    ProviderStreamChunk(delta_text="安"),
    ProviderStreamChunk(
        usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        finish_reason=FinishReason.STOP,
    ),
)


def _session(tmp_path: Path) -> SessionService:
    sessions = tmp_path / "sessions"
    service = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        id_factory=lambda: _SID,
    )
    service.start()
    return service


def _events(tmp_path: Path) -> list[SessionEvent]:
    return JsonlEventStore(tmp_path / "sessions").read(_SID)


def _gateway(provider: FakeModelProvider) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek", model="deepseek-chat", context_window=65536
            ),
        )
    )
    return DefaultLlmGateway(
        registry, resolver=DefaultModelSelectionResolver(catalog, current_model=_REF)
    )


def test_streamed_turn_records_full_text_and_usage(tmp_path: Path) -> None:
    session = _session(tmp_path)
    provider = FakeModelProvider(capabilities=_STREAMING, stream_chunks=_CHUNKS)
    gateway = _gateway(provider)
    meter = UsageMeter(CostEstimator(InMemoryModelCatalog(())), clock=lambda: "t0")
    deltas: list[str] = []
    agent = AgentTurnService(
        session,
        loop_factory=lambda: BuiltinAgentLoop(gateway, meter, on_delta=deltas.append),
    )

    response = agent.handle_user_message("你好")

    assert deltas == ["早", "安"]
    assert response.status is TurnStatus.COMPLETED
    assert response.text == "早安"
    events = _events(tmp_path)
    assert [e.type for e in events][-2:] == [
        EventType.ASSISTANT_MESSAGE,
        EventType.USAGE_RECORDED,
    ]
    assert events[-2].payload["text"] == "早安"
    assert events[-1].payload["total_tokens"] == 5


def test_cancel_mid_stream_persists_partial_with_marker_then_session_continues(
    tmp_path: Path,
) -> None:
    """取消轮语义：部分文本 + 取消标记落盘（FAILED / user_cancelled / 估算 usage），
    取消轮文本进入历史，下一轮正常完成。"""
    session = _session(tmp_path)
    provider = FakeModelProvider(capabilities=_STREAMING, stream_chunks=_CHUNKS)
    gateway = _gateway(provider)
    meter = UsageMeter(CostEstimator(InMemoryModelCatalog(())), clock=lambda: "t0")
    source = TurnCancelSource()
    cancel_next = False

    def sink(delta: str) -> None:
        if cancel_next:
            token = source.current()
            assert token is not None
            token.cancel()

    agent = AgentTurnService(
        session,
        loop_factory=lambda: BuiltinAgentLoop(
            gateway, meter, on_delta=sink, cancel_token_factory=source.current
        ),
    )

    # 第一轮：首个增量后取消（sink 在收到「早」后置位 token，「安」不再送达）。
    cancel_next = True
    source.issue()
    first = agent.handle_user_message("讲个故事")
    source.clear()

    assert first.status is TurnStatus.FAILED
    assert first.text == "早\n（本轮回复已被用户取消）"
    events = _events(tmp_path)
    assistant = next(
        e
        for e in events
        if e.type is EventType.ASSISTANT_MESSAGE and e.payload["turn_id"] == "turn_0001"
    )
    assert assistant.payload["status"] == "failed"
    assert assistant.payload["stop_reason"] == "user_cancelled"
    usage = next(e for e in events if e.type is EventType.USAGE_RECORDED)
    assert usage.payload["estimated"] is True

    # 第二轮：不取消，session 继续且取消轮文本（带标记）进入历史。
    cancel_next = False
    source.issue()
    second = agent.handle_user_message("继续")
    source.clear()

    assert second.status is TurnStatus.COMPLETED
    assert second.text == "早安"
    assert provider.last_request is not None
    texts = [
        block.text
        for message in provider.last_request.messages
        for block in message.content
        if hasattr(block, "text")
    ]
    assert texts == ["讲个故事", "早\n（本轮回复已被用户取消）", "继续"]
