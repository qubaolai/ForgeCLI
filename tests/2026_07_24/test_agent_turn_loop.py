"""AgentTurnService 驱动 BuiltinAgentLoop：单轮 chat 验收 + 驱动器健壮性。

对应 ADR-0010 §5 时序与 §13(4-5) 切片。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.agent_loop import (
    AgentLoop,
    AnswerAction,
    BuiltinAgentLoop,
    LoopDecision,
    LoopInput,
    LoopObservation,
    LoopStepResult,
    LoopStop,
    LoopStopReason,
    ToolRequest,
    ToolRequestAction,
)
from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.llm.gateway import (
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelCatalogEntry,
    ModelUsage,
    ProviderRegistry,
)
from forgecli.application.llm.metering import CostEstimator, UsageMeter
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.session import EventType, SessionEvent, SessionService
from forgecli.domain.conversation import TurnStatus
from forgecli.infrastructure.llm.adapters import FakeModelProvider
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore

_SID = "sid"
_REF = ModelRef(provider="deepseek", model="deepseek-chat")


def _session(tmp_path: Path) -> SessionService:
    sessions = tmp_path / "sessions"
    service = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        clock=lambda: "2026-07-24T10:00:00+08:00",
        id_factory=lambda: _SID,
    )
    service.start()
    return service


def _events(tmp_path: Path) -> list[SessionEvent]:
    return JsonlEventStore(tmp_path / "sessions").read(_SID)


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


def _service(session: SessionService, provider: FakeModelProvider) -> AgentTurnService:
    gateway = _gateway(provider)
    meter = UsageMeter(CostEstimator(_catalog()), clock=lambda: "t0")
    return AgentTurnService(
        session,
        loop_factory=lambda: BuiltinAgentLoop(
            gateway, meter, request_id_factory=lambda: "req_fixed"
        ),
    )


def test_single_turn_chat_writes_paired_events_and_usage(tmp_path: Path) -> None:
    """07-24 验收：单轮 chat 经 loop 完成，成对落盘 + usage 由 turn service 写入。"""
    session = _session(tmp_path)
    provider = FakeModelProvider(
        content="答",
        usage=ModelUsage(input_tokens=1000, output_tokens=500, total_tokens=1500),
    )

    response = _service(session, provider).handle_user_message("你好")

    assert response.status is TurnStatus.COMPLETED
    assert response.text == "答"
    events = _events(tmp_path)
    assert [e.type for e in events] == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
        EventType.USAGE_RECORDED,
    ]
    assistant = events[2]
    assert assistant.payload["turn_id"] == "turn_0001"
    assert assistant.payload["status"] == "completed"
    assert assistant.payload["stop_reason"] == "final_answer"
    usage = events[3].payload
    assert usage["turn_id"] == "turn_0001"
    assert usage["request_id"] == "req_fixed"
    assert usage["provider"] == "deepseek"
    assert usage["model"] == "deepseek-chat"
    assert usage["estimated"] is False
    assert usage["estimated_cost"] == 1000 * 0.2 / 1000 + 500 * 0.4 / 1000


class _ScriptedLoop(AgentLoop):
    """按脚本产出步进结果的 loop 测试替身；记录收到的输入与观察。"""

    def __init__(self, steps: list[LoopStepResult]) -> None:
        self._steps = steps
        self.loop_input: LoopInput | None = None
        self.observations: list[LoopObservation] = []

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        self.loop_input = loop_input
        return self._steps.pop(0)

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        self.observations.append(observation)
        return self._steps.pop(0)


def test_unsupported_action_fails_turn(tmp_path: Path) -> None:
    loop = _ScriptedLoop(
        [LoopDecision(next_action=ToolRequestAction(ToolRequest(name="fs_read")))]
    )
    agent = AgentTurnService(_session(tmp_path), loop_factory=lambda: loop)

    response = agent.handle_user_message("读文件")

    assert response.status is TurnStatus.FAILED
    assert "尚未支持" in response.text


def test_never_stopping_loop_hits_safety_bound(tmp_path: Path) -> None:
    class _Spinner(AgentLoop):
        def __init__(self) -> None:
            self.observe_calls = 0

        def start(self, loop_input: LoopInput) -> LoopStepResult:
            return LoopDecision(next_action=None)

        def observe(self, observation: LoopObservation) -> LoopStepResult:
            self.observe_calls += 1
            return LoopDecision(next_action=None)

    spinner = _Spinner()
    agent = AgentTurnService(_session(tmp_path), loop_factory=lambda: spinner)

    response = agent.handle_user_message("转圈")

    assert response.status is TurnStatus.FAILED
    assert "安全上限" in response.text
    assert spinner.observe_calls <= 8


def test_final_answer_without_answer_is_defensive_failure(tmp_path: Path) -> None:
    loop = _ScriptedLoop([LoopStop.of(LoopStopReason.FINAL_ANSWER)])
    agent = AgentTurnService(_session(tmp_path), loop_factory=lambda: loop)

    response = agent.handle_user_message("x")

    assert response.status is TurnStatus.FAILED
    assert "未产出回复" in response.text


def test_blocking_stop_persists_message_without_usage(tmp_path: Path) -> None:
    loop = _ScriptedLoop(
        [
            LoopStop.of(
                LoopStopReason.MODEL_ERROR_BLOCKING, message="认证失败：检查 key。"
            )
        ]
    )
    agent = AgentTurnService(_session(tmp_path), loop_factory=lambda: loop)

    response = agent.handle_user_message("x")

    assert response.status is TurnStatus.FAILED
    assert response.text == "认证失败：检查 key。"
    events = _events(tmp_path)
    assert [e.type for e in events][-1] is EventType.ASSISTANT_MESSAGE  # 无 usage 事件
    assert events[-1].payload["stop_reason"] == "model_error_blocking"


def test_loop_factory_called_once_per_turn(tmp_path: Path) -> None:
    calls = 0

    def factory() -> AgentLoop:
        nonlocal calls
        calls += 1
        return _ScriptedLoop(
            [
                LoopDecision(next_action=AnswerAction(text="好")),
                LoopStop.of(LoopStopReason.FINAL_ANSWER),
            ]
        )

    agent = AgentTurnService(_session(tmp_path), loop_factory=factory)
    agent.handle_user_message("a")
    agent.handle_user_message("b")

    assert calls == 2


def test_history_reaches_provider_and_includes_failed_turn(tmp_path: Path) -> None:
    """多轮历史进入下一轮 LoopInput；失败轮文本按新规则同样进入历史。"""
    session = _session(tmp_path)
    loops = [
        _ScriptedLoop(
            [LoopStop.of(LoopStopReason.MODEL_ERROR_BLOCKING, message="上轮失败文案")]
        ),
        _ScriptedLoop(
            [
                LoopDecision(next_action=AnswerAction(text="继续")),
                LoopStop.of(LoopStopReason.FINAL_ANSWER),
            ]
        ),
    ]
    agent = AgentTurnService(session, loop_factory=lambda: loops.pop(0))

    first = agent.handle_user_message("第一问")
    second_loop = loops[0]
    second = agent.handle_user_message("第二问")

    assert first.status is TurnStatus.FAILED
    assert second.status is TurnStatus.COMPLETED
    assert second_loop.loop_input is not None
    texts = [
        block.text
        for message in second_loop.loop_input.context_package.messages
        for block in message.content
        if hasattr(block, "text")
    ]
    # 历史镜像事件重放：user1 + 失败轮文案 + user2
    assert texts == ["第一问", "上轮失败文案", "第二问"]


def test_real_loop_multi_turn_history_reaches_provider(tmp_path: Path) -> None:
    session = _session(tmp_path)
    provider = FakeModelProvider(content="答")
    agent = _service(session, provider)

    agent.handle_user_message("一")
    agent.handle_user_message("二")

    assert provider.last_request is not None
    # 第二轮请求携带：user1 + assistant1 + user2
    assert len(provider.last_request.messages) == 3
