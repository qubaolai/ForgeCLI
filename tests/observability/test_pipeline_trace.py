"""工具管线的日志留痕 (ADR-0035 决策 1).

走的是组合根真正装配出来的那套栈, 不是替身: 这组用例要证明的是"生产链路上确实留下了
这些事实". 用替身拼一条链路也能让断言通过, 但证明不了任何事.

一次工具调用最常被问的三个问题, 每一个都对应这里的一条断言: 它收到的入参是什么,
安全层怎么裁的, 以及它到底改了哪个文件.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.session.session_service import SessionService
from forgecli.application.tool_request.observations import (
    ObservationKind,
    ToolObservation,
)
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.intents import SessionMode
from forgecli.domain.model.request import ModelRequest, StructuredModelRequest
from forgecli.domain.model.response import ModelResponse, StructuredModelResponse
from forgecli.domain.model.streaming import ModelStreamChunk
from forgecli.domain.security.context import PolicyContext
from forgecli.infrastructure.session.json_state_store import JsonStateStore
from forgecli.infrastructure.session.jsonl_event_store import JsonlEventStore
from forgecli.interfaces.runtime.tool_wiring import ToolStack, build_tool_stack


class _UnusedGateway(LlmGateway):
    """本组用例不调模型."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("本用例不该调用模型")

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        raise AssertionError("本用例不该调用模型")

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        raise AssertionError("本用例不该调用模型")


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ToolStack:
    home = tmp_path / "forge-home"
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(home))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sessions = home / "sessions"
    session = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root=str(workspace),
    )
    session.start()
    return build_tool_stack(
        workspace_roots=(str(workspace),),
        workspace_id="ws-logging",
        session=session,
        gateway=_UnusedGateway(),
        run_bus=AgentRunEventBus(),
    )


def _call(stack: ToolStack, name: str, **arguments: object) -> ToolObservation:
    mode = SessionMode.ACCEPT_EDITS
    return stack.coordinator.handle(
        ToolRequest(name=name, arguments=arguments),
        context=replace(stack.context_factory(), fence=stack.fence_factory(mode)),
        policy=PolicyContext(
            mode=mode,
            session_id="sess-1",
            turn_id="turn-1",
            execution_profile_hash=stack.profile.execution_profile_hash,
            fence=stack.fence_factory(mode),
            confined=stack.confined,
        ),
    )


def _line(records: pytest.LogCaptureFixture, event: str) -> str:
    return next(
        message
        for message in (record.getMessage() for record in records.records)
        if message.startswith(f"{event} ")
    )


def _events(records: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage().split(" ", 1)[0] for record in records.records]


def test_a_write_leaves_the_whole_pipeline_in_the_log(
    stack: ToolStack, records: pytest.LogCaptureFixture
) -> None:
    result = _call(stack, "fs.apply_patch", patch="*** NEW notes.txt\nv = 1\n")

    assert result.kind is ObservationKind.TOOL_RESULT
    events = _events(records)
    for expected in (
        "pipeline.start",  # 请求进来时的入参与模式
        "pipeline.prepared",  # 收缩后的能力与读写目标
        "pipeline.decision",  # 裁决结论与理由
        "recovery.begin.ok",  # 恢复保障建立在签发授权之前
        "security.authorization_issued",
        "tool.execute.ok",
        "pipeline.ok",
    ):
        assert expected in events, f"{expected} 不在 {events}"


def test_prepared_line_names_the_files_that_will_change(
    stack: ToolStack, records: pytest.LogCaptureFixture
) -> None:
    """ "它到底改了哪个文件"必须能从一行里读出来, 而不是靠事后 diff 工作区."""
    _call(stack, "fs.apply_patch", patch="*** NEW notes.txt\nv = 1\n")

    line = _line(records, "pipeline.prepared")
    assert "notes.txt" in line
    assert "capabilities=" in line


def test_invocation_id_ties_the_lines_together(
    stack: ToolStack, records: pytest.LogCaptureFixture
) -> None:
    """同一次调用的每一行都带同一个 inv: 少了它, 并发或连续调用就分不开."""
    _call(stack, "fs.apply_patch", patch="*** NEW notes.txt\nv = 1\n")

    identifiers = {
        field
        for message in (record.getMessage() for record in records.records)
        for field in message.split()
        if field.startswith("inv=")
    }
    assert len(identifiers) == 1


def test_a_rejected_call_says_why(
    stack: ToolStack, records: pytest.LogCaptureFixture
) -> None:
    """被拒的调用最需要留痕: 模型拿到结论继续往下走, 屏幕上只有一行摘要."""
    result = _call(stack, "fs.no_such_tool", path="a.py")

    assert result.kind is ObservationKind.TOOL_UNAVAILABLE
    assert "pipeline.unavailable" in _events(records)
    assert "reason_code=" in _line(records, "pipeline.rejected")
