"""接线冒烟：build_llm_runtime 交回计量件，bootstrap 同款组装的 loop 能处理一轮。

真实装配（OpenAI adapter + 凭证池）但不发网络：无凭证时经 loop 归一为
FAILED 的可行动文案，事件仍成对落盘（失败隔离 + §19 无凭证不发请求）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from forgecli.application.agent_loop import BuiltinAgentLoop, LoopEventBus
from forgecli.application.agent_turn import AgentTurnService, TurnCancelSource
from forgecli.application.config.config_service import ConfigService
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.metering import UsageMeter
from forgecli.application.session import EventType, SessionService
from forgecli.domain.conversation import TurnStatus
from forgecli.infrastructure.config.toml_store import TomlConfigStore
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore
from forgecli.interfaces.cli.llm_wiring import build_llm_runtime


def test_runtime_composes_loop_turn_without_network(tmp_path: Path) -> None:
    if os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("本机设置了 DEEPSEEK_API_KEY，跳过无凭证断言")

    llm_toml = tmp_path / "llm.toml"
    llm_toml.write_text(
        "[llm.providers.deepseek.models.deepseek-chat]\ncontext_window = 65536\n",
        encoding="utf-8",
    )
    forge_toml = tmp_path / "forge.toml"
    forge_toml.write_text(
        '[model]\nprovider = "deepseek"\nname = "deepseek-chat"\n', encoding="utf-8"
    )
    config_service = ConfigService(
        TomlConfigStore(tmp_path / "config.toml"), TomlConfigStore(forge_toml)
    )
    llm_service = LlmConfigService(TomlLlmConfigStore(llm_toml))
    runtime = build_llm_runtime(config_service, llm_service, forge_toml)
    assert isinstance(runtime.usage_meter, UsageMeter)

    sessions = tmp_path / "sessions"
    session = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        id_factory=lambda: "sid",
    )
    session.start()

    # 与 bootstrap 同款组装：流式增量外送 + 取消源 + 事件总线。
    deltas: list[str] = []
    cancel_source = TurnCancelSource()
    agent = AgentTurnService(
        session,
        loop_factory=lambda: BuiltinAgentLoop(
            runtime.gateway,
            runtime.usage_meter,
            cancel_token_factory=cancel_source.current,
            on_delta=deltas.append,
            event_bus=LoopEventBus(),
        ),
    )

    response = agent.handle_user_message("你好")

    # 无凭证：认证错误在发网络前归一为 FAILED 可行动文案（不崩溃、不挂起）。
    assert response.status is TurnStatus.FAILED
    assert "认证失败" in response.text
    events = JsonlEventStore(sessions).read("sid")
    assert [e.type for e in events] == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
    ]
