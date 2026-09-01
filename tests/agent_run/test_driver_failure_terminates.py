"""驱动抛异常时, 运行事件流也必须收尾.

实测故障: 一次 `NameError` 让整轮在第一次模型调用就炸了. 后端表现完全正确 —— 日志里
有 `turn.driver_failed`, 会话事件成对落了盘, 用户可见文本是那句"助手处理出错". 但页面
一直显示"处理中", 直到用户自己刷新。

原因是循环的 `_publish_turn_finished` 只在 `_stop()` 里调, 而抛异常根本走不到那儿. 于是
**会话事件有终态, 运行事件流没有** —— 而页面听的正是后者。

这个文件钉的是那条缺口: 无论循环怎么死, 运行事件流都要有一条终态。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.prompt.system_prompt_builder import SystemPromptBuilder
from forgecli.application.session.session_service import SessionService
from forgecli.domain.agent.run_events import AgentRunEvent, AgentRunEventKind
from forgecli.domain.conversation.turn import TurnStatus
from forgecli.infrastructure.session.json_state_store import JsonStateStore
from forgecli.infrastructure.session.jsonl_event_store import JsonlEventStore
from support.fakes import FACTS, NoProjectInstructions


class _Collector:
    def __init__(self) -> None:
        self.events: list[AgentRunEvent] = []

    def on_event(self, event: AgentRunEvent) -> None:
        self.events.append(event)


def _exploding_loop():
    raise RuntimeError("循环装配就炸了")


@pytest.fixture
def collected(tmp_path: Path) -> tuple[AgentTurnService, _Collector]:
    bus = AgentRunEventBus()
    collector = _Collector()
    bus.subscribe(collector)  # type: ignore[arg-type]
    session = SessionService(
        JsonlEventStore(tmp_path),
        JsonStateStore(tmp_path),
        workspace_root=str(tmp_path),
    )
    session.start()
    service = AgentTurnService(
        session,
        loop_factory=_exploding_loop,  # type: ignore[arg-type]
        prompt_builder=SystemPromptBuilder(),
        runtime_facts=lambda: FACTS,
        instructions=NoProjectInstructions(),
        context_budget=lambda: None,
        run_bus=bus,
    )
    return service, collector


def test_a_driver_failure_still_ends_the_run_event_stream(collected) -> None:
    """页面据此停止转圈. 少了它, 后端全对而用户看到的是永远的"处理中"。"""
    service, collector = collected

    service.handle_user_message("随便什么")

    terminal = [
        event
        for event in collector.events
        if event.kind
        in (
            AgentRunEventKind.TURN_COMPLETED,
            AgentRunEventKind.TURN_FAILED,
            AgentRunEventKind.TURN_CANCELLED,
        )
    ]
    assert len(terminal) == 1
    assert terminal[0].kind is AgentRunEventKind.TURN_FAILED


def test_the_failure_reason_travels_with_it(collected) -> None:
    """ "处理中"变成"失败了"还不够 —— 用户要知道失败在哪, 否则只能来问。"""
    service, collector = collected

    service.handle_user_message("随便什么")

    failed = next(
        event
        for event in collector.events
        if event.kind is AgentRunEventKind.TURN_FAILED
    )
    assert "RuntimeError" in failed.payload.detail  # type: ignore[union-attr]
    assert "循环装配就炸了" in failed.payload.detail  # type: ignore[union-attr]


def test_the_turn_is_still_recorded_as_failed(collected) -> None:
    """失败隔离照旧: 驱动炸了不破坏会话, 仍成对落盘 assistant。"""
    service, _ = collected

    response = service.handle_user_message("随便什么")

    assert response.status is TurnStatus.FAILED
    assert "助手处理出错" in response.text
