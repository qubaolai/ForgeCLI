"""AgentTurnService 经 LlmGateway 完成 chat turn（ADR-0011 §8，2026-07-07 集成）。

覆盖：单轮 chat 写 user/assistant/usage 事件、失败隔离（网关错误 -> FAILED +
可行动提示 + 不写 usage）、多轮 transcript 携带、gateway 不落盘（事件只经
SessionService）。全部走 FakeModelProvider，离线确定性。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.agent_turn import AgentTurnService, GatewayReplier
from forgecli.application.llm.gateway import (
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelAuthError,
    ModelCatalogEntry,
    ModelUsage,
    ProviderRegistry,
)
from forgecli.application.llm.metering import CostEstimator, UsageMeter
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.session import EventType, SessionService
from forgecli.domain.conversation import TurnStatus
from forgecli.infrastructure.llm.adapters import FakeModelProvider
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


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


def _service(
    tmp_path: Path, provider: FakeModelProvider
) -> tuple[AgentTurnService, SessionService, Path]:
    sessions = tmp_path / "sessions"
    session = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        id_factory=lambda: "sid",
    )
    session.start()
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = _catalog()
    gateway = DefaultLlmGateway(
        registry, resolver=DefaultModelSelectionResolver(catalog, current_model=_REF)
    )
    replier = GatewayReplier(
        gateway,
        UsageMeter(CostEstimator(catalog), clock=lambda: "t0"),
        request_id_factory=lambda: "req_fixed",
    )
    return AgentTurnService(session, replier=replier), session, sessions


def test_chat_turn_writes_user_assistant_and_usage_events(tmp_path: Path) -> None:
    usage = ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    agent, _, sessions = _service(
        tmp_path, FakeModelProvider(content="你好，我是助手", usage=usage)
    )
    response = agent.handle_user_message("你好")
    assert response.status is TurnStatus.COMPLETED
    assert response.text == "你好，我是助手"

    events = JsonlEventStore(sessions).read("sid")
    assert [e.type for e in events] == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
        EventType.USAGE_RECORDED,
    ]
    usage_event = events[-1]
    assert usage_event.payload["turn_id"] == "turn_0001"
    assert usage_event.payload["request_id"] == "req_fixed"
    assert usage_event.payload["provider"] == "deepseek"
    assert usage_event.payload["model"] == "deepseek-chat"
    assert usage_event.payload["input_tokens"] == 10
    assert usage_event.payload["estimated"] is False
    assert usage_event.payload["estimated_cost"] == 10 * 0.2 / 1000 + 5 * 0.4 / 1000


def test_gateway_error_isolated_as_failed_turn_without_usage(tmp_path: Path) -> None:
    agent, _, sessions = _service(
        tmp_path, FakeModelProvider(error=ModelAuthError("401 无效 key"))
    )
    response = agent.handle_user_message("你好")
    assert response.status is TurnStatus.FAILED
    assert "认证失败" in response.text  # §12：给用户可行动提示
    assert "401" in response.text

    events = JsonlEventStore(sessions).read("sid")
    types = [e.type for e in events]
    assert EventType.ASSISTANT_MESSAGE in types
    assert EventType.USAGE_RECORDED not in types


def test_multi_turn_history_flows_to_provider(tmp_path: Path) -> None:
    provider = FakeModelProvider(content="回答")
    agent, _, _ = _service(tmp_path, provider)
    agent.handle_user_message("第一问")
    agent.handle_user_message("第二问")
    assert provider.last_request is not None
    # 第二轮请求包含：第一问 + 第一答 + 第二问。
    assert len(provider.last_request.messages) == 3


def test_failed_turn_not_polluting_history(tmp_path: Path) -> None:
    provider = FakeModelProvider(error=ModelAuthError("401"))
    agent, _, _ = _service(tmp_path, provider)
    agent.handle_user_message("失败的一轮")
    provider._error = None  # noqa: SLF001  # 恢复：下一轮成功
    agent.handle_user_message("成功的一轮")
    assert provider.last_request is not None
    # 历史里有：失败轮的 user 消息 + 本轮 user 消息；错误提示不进上下文。
    texts = [
        block.text  # type: ignore[attr-defined]
        for message in provider.last_request.messages
        for block in message.content
    ]
    assert texts == ["失败的一轮", "成功的一轮"]


def test_stub_path_still_works_without_replier(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    session = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        id_factory=lambda: "sid",
    )
    session.start()
    agent = AgentTurnService(session)
    response = agent.handle_user_message("hi")
    assert response.status is TurnStatus.COMPLETED
    assert "LLM 接入开发中" in response.text
    types = [e.type for e in JsonlEventStore(sessions).read("sid")]
    assert EventType.USAGE_RECORDED not in types
