"""ADR-0011 §18 集成矩阵：current model 路由 -> 用途覆盖 -> 能力错误 -> usage 落盘。

用真实的动态解析器（ConfigBackedSelectionResolver）+ 真实凭证池 + FakeModelProvider
组成端到端链路（不打网络），覆盖 §18 里跨组件协作的场景；单组件行为在各切片
测试中已覆盖。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.agent_loop import BuiltinAgentLoop
from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.config import config_keys
from forgecli.application.config.config_service import ConfigService
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    ExplicitModelSelection,
    ModelBadRequestError,
    ModelParams,
    ModelRequest,
    ModelUsage,
    ProviderRegistry,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.metering import CostEstimator, UsageMeter
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.overrides_service import ModelOverridesService
from forgecli.application.llm.runtime_resolver import ConfigBackedSelectionResolver
from forgecli.application.llm.thinking import ThinkingEffortName, ThinkingMode
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
from forgecli.application.session import EventType, SessionService
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.config.toml_store import TomlConfigStore
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.infrastructure.llm.adapters import FakeModelProvider
from forgecli.infrastructure.llm.overrides_toml_store import TomlModelOverridesStore
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore

_LLM_TOML = """
[llm.providers.deepseek.models.deepseek-chat]
context_window = 65536
cost_per_1k_input = 0.2
cost_per_1k_output = 0.4
thinking_mode = "off"

[llm.providers.deepseek.models.deepseek-mini]
context_window = 8192
thinking_mode = "on"
thinking_effort = "high"
thinking_efforts = ["high", "max"]
thinking_default_effort = "high"
"""


class _Env:
    def __init__(self, tmp_path: Path, provider: FakeModelProvider) -> None:
        llm_toml = tmp_path / "llm.toml"
        llm_toml.write_text(_LLM_TOML, encoding="utf-8")
        forge_toml = tmp_path / "forge.toml"
        self.llm = LlmConfigService(TomlLlmConfigStore(llm_toml))
        self.config = ConfigService(
            TomlConfigStore(tmp_path / "config.toml"), TomlConfigStore(forge_toml)
        )
        self.overrides = ModelOverridesService(
            TomlModelOverridesStore(forge_toml), self.llm
        )
        self.thinking_state = ThinkingRuntimeState()
        registry = ProviderRegistry()
        registry.register(provider)
        self.gateway = DefaultLlmGateway(
            registry,
            resolver=ConfigBackedSelectionResolver(
                config_service=self.config,
                llm_config_service=self.llm,
                overrides_loader=self.overrides.overrides,
                thinking_state=self.thinking_state,
            ),
        )
        self.provider = provider

    def set_current(self, model: str) -> None:
        self.config.set(config_keys.DEFAULT_MODEL_PROVIDER_KEY, "deepseek")
        self.config.set(config_keys.DEFAULT_MODEL_NAME_KEY, model)


def _request(origin: RequestOrigin = RequestOrigin.CHAT) -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=origin,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )


def test_current_model_routes_to_configured_provider_model(tmp_path: Path) -> None:
    env = _Env(tmp_path, FakeModelProvider(content="ok"))
    env.set_current("deepseek-chat")
    response = env.gateway.complete(_request())
    assert (response.provider, response.model) == ("deepseek", "deepseek-chat")


def test_switching_current_model_switches_thinking_config(tmp_path: Path) -> None:
    env = _Env(tmp_path, FakeModelProvider(content="ok"))
    env.set_current("deepseek-chat")
    env.gateway.complete(_request(RequestOrigin.PLAN))
    assert env.provider.last_request is not None
    assert env.provider.last_request.thinking is not None
    assert env.provider.last_request.thinking.enabled is False

    env.set_current("deepseek-mini")
    env.gateway.complete(_request(RequestOrigin.PLAN))
    assert env.provider.last_request is not None
    assert env.provider.last_request.thinking is not None
    assert env.provider.last_request.thinking.enabled is True
    assert env.provider.last_request.thinking.effort == ThinkingEffortName("high")


def test_runtime_thinking_override_reaches_gateway_without_writing_llm_config(
    tmp_path: Path,
) -> None:
    env = _Env(tmp_path, FakeModelProvider(content="ok"))
    env.set_current("deepseek-chat")
    before = (tmp_path / "llm.toml").read_text(encoding="utf-8")
    ref = ModelRef("deepseek", "deepseek-chat")
    entry = build_catalog(env.llm.config()).get(ref)

    assert env.thinking_state.update(ref, entry, mode=ThinkingMode.ON) is True
    env.gateway.complete(_request())

    assert (tmp_path / "llm.toml").read_text(encoding="utf-8") == before
    assert env.provider.last_request is not None
    assert env.provider.last_request.thinking is not None
    assert env.provider.last_request.thinking.enabled is True


def test_origin_override_routes_only_that_origin(tmp_path: Path) -> None:
    env = _Env(tmp_path, FakeModelProvider(content="ok"))
    env.set_current("deepseek-chat")
    env.overrides.set_override(
        RequestOrigin.TITLE, ModelRef(provider="deepseek", model="deepseek-mini")
    )
    assert env.gateway.complete(_request(RequestOrigin.TITLE)).model == "deepseek-mini"
    assert env.gateway.complete(_request(RequestOrigin.CHAT)).model == "deepseek-chat"


def test_capability_shortfall_errors_without_model_switch(tmp_path: Path) -> None:
    env = _Env(tmp_path, FakeModelProvider(content="ok"))
    env.set_current("deepseek-chat")
    request = ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
        # deepseek-chat 的 thinking_mode=off，不能满足 thinking 能力诉求。
        required_capabilities=("thinking",),
    )
    with pytest.raises(ModelBadRequestError):
        env.gateway.complete(request)
    assert env.provider.complete_calls == 0  # 不发起调用、不自动换模型


def test_explicit_selection_bypasses_override_but_validated(tmp_path: Path) -> None:
    env = _Env(tmp_path, FakeModelProvider(content="ok"))
    env.set_current("deepseek-chat")
    request = ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=ExplicitModelSelection(
            provider="deepseek", model="deepseek-mini"
        ),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )
    assert env.gateway.complete(request).model == "deepseek-mini"


def test_full_chain_chat_turn_persists_usage_with_cost(tmp_path: Path) -> None:
    usage = ModelUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
    env = _Env(tmp_path, FakeModelProvider(content="答", usage=usage))
    env.set_current("deepseek-chat")

    sessions = tmp_path / "sessions"
    session = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        id_factory=lambda: "sid",
    )
    session.start()
    meter = UsageMeter(
        CostEstimator(build_catalog(env.llm.config())), clock=lambda: "t0"
    )
    agent = AgentTurnService(
        session, loop_factory=lambda: BuiltinAgentLoop(env.gateway, meter)
    )
    response = agent.handle_user_message("你好")
    assert response.text == "答"

    events = JsonlEventStore(sessions).read("sid")
    usage_events = [e for e in events if e.type == EventType.USAGE_RECORDED]
    assert len(usage_events) == 1
    payload = usage_events[0].payload
    assert payload["estimated"] is False
    assert payload["estimated_cost"] == 1000 * 0.2 / 1000 + 500 * 0.4 / 1000
    assert payload["provider"] == "deepseek"
