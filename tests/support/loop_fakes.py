"""BuiltinAgentLoop 边界测试用的替身.

只替换循环之外的东西: 网关, 计量, 事件订阅者, 工作区快照, 工具目录. 循环自己一行不动.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field

from forgecli.application.agent_run.events import (
    AgentRunEvent,
    AgentRunEventBus,
    AgentRunEventKind,
    AgentRunEventSubscriber,
)
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.domain.agent.state import AssembledContext, LoopInput
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.intents import SessionMode
from forgecli.domain.model.request import ModelRequest, StructuredModelRequest
from forgecli.domain.model.response import (
    FinishReason,
    ModelResponse,
    ModelUsage,
    StructuredModelResponse,
)
from forgecli.domain.model.streaming import ModelStreamChunk, ToolCallDelta
from forgecli.domain.model.usage import UnitPrices, UsageRecordDraft
from forgecli.domain.prompt.blocks import PromptBlock, PromptBlockId, PromptSnapshot
from forgecli.domain.tool.tool_call import ToolCall, ToolSchema
from forgecli.domain.workspace.changes import WorkspaceFileState, WorkspaceSnapshot

PROVIDER = "fake"
MODEL = "fake-model"


# ---- 网关脚本 ----


@dataclass(frozen=True)
class Reply:
    """模型这次说了什么. 参数写成 JSON 文本, 好模拟坏掉的参数."""

    text: str = ""
    tool_calls: tuple[tuple[str, str, str], ...] = ()  # (id, name, arguments_json)
    finish_reason: FinishReason = FinishReason.STOP


@dataclass(frozen=True)
class Fail:
    """这次调用抛这个错."""

    error: Exception


@dataclass(frozen=True)
class Interrupted:
    """流式中途断掉: 先吐 text, 收尾块带 interrupted."""

    text: str = ""
    finish_reason: FinishReason = FinishReason.USER_CANCELLED


Step = Reply | Fail | Interrupted


class ScriptedGateway(LlmGateway):
    """按脚本逐次回应. 脚本用完还被调用就是测试写错了."""

    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.requests: list[ModelRequest] = []

    def _next(self, request: ModelRequest) -> Step:
        self.requests.append(request)
        if not self._steps:
            raise AssertionError(
                f"网关脚本已用完, 第 {len(self.requests)} 次调用没有回应"
            )
        return self._steps.pop(0)

    def complete(self, request: ModelRequest) -> ModelResponse:
        step = self._next(request)
        if isinstance(step, Fail):
            raise step.error
        if isinstance(step, Interrupted):
            raise AssertionError("非流式路径不支持 Interrupted")
        return ModelResponse(
            request_id=request.request_id,
            provider=PROVIDER,
            model=MODEL,
            content=step.text,
            finish_reason=step.finish_reason,
            usage=_usage(),
            latency_ms=1.0,
            tool_calls=tuple(
                ToolCall(tool_call_id=i, name=n, arguments=json.loads(a))
                for i, n, a in step.tool_calls
            ),
        )

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        raise AssertionError("循环不走结构化输出")

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        # 不写成生成器: 抛错要在调用那一刻发生, 而不是在第一次 next 的时候.
        step = self._next(request)
        if isinstance(step, Fail):
            raise step.error
        return iter(self._chunks(request.request_id, step))

    @staticmethod
    def _chunks(request_id: str, step: Reply | Interrupted) -> list[ModelStreamChunk]:
        def chunk(sequence: int, **fields: object) -> ModelStreamChunk:
            return ModelStreamChunk(
                request_id=request_id,
                sequence=sequence,
                provider=PROVIDER,
                model=MODEL,
                **fields,  # type: ignore[arg-type]
            )

        if isinstance(step, Interrupted):
            return [
                chunk(0, delta_text=step.text or None),
                chunk(
                    1,
                    usage_delta=_usage(),
                    finish_reason=step.finish_reason,
                    interrupted=True,
                ),
            ]
        deltas = tuple(
            ToolCallDelta(index=k, tool_call_id=i, name=n, arguments_delta=a)
            for k, (i, n, a) in enumerate(step.tool_calls)
        )
        return [
            chunk(0, delta_text=step.text or None, tool_call_deltas=deltas),
            chunk(1, usage_delta=_usage(), finish_reason=step.finish_reason),
        ]


def _usage() -> ModelUsage:
    return ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15)


class FakeMeter:
    """把请求与响应拼成一份草稿, 单价全空."""

    def build_draft(
        self, request: ModelRequest, response: ModelResponse
    ) -> UsageRecordDraft:
        return UsageRecordDraft(
            request_id=response.request_id,
            session_id=request.session_id,
            turn_id=request.turn_id,
            provider=response.provider,
            model=response.model,
            origin=request.origin,
            usage=response.usage,
            estimated=response.usage.estimated,
            unit_prices=UnitPrices(),
            estimated_cost=None,
            latency_ms=response.latency_ms,
            created_at="2026-01-01T00:00:00Z",
        )


# ---- 事件 ----


class RecordingSubscriber(AgentRunEventSubscriber):
    def __init__(self) -> None:
        self.events: list[AgentRunEvent] = []

    def on_event(self, event: AgentRunEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[AgentRunEventKind]:
        return [event.kind for event in self.events]

    def of(self, kind: AgentRunEventKind) -> list[AgentRunEvent]:
        return [event for event in self.events if event.kind is kind]


def recording_bus() -> tuple[AgentRunEventBus, RecordingSubscriber]:
    bus = AgentRunEventBus()
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    return bus, recorder


# ---- 工作区 ----


class FakeSnapshotProvider:
    """测试直接改 files 来模拟文件变化."""

    def __init__(self) -> None:
        self.files: dict[str, WorkspaceFileState] = {}

    def touch(self, path: str, size: int = 1) -> None:
        previous = self.files.get(path)
        mtime = 1 if previous is None else previous.mtime_ns + 1
        self.files[path] = WorkspaceFileState(path=path, size=size, mtime_ns=mtime)

    def remove(self, path: str) -> None:
        self.files.pop(path, None)

    def snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(files=tuple(self.files.values()))


# ---- 工具目录与输入 ----


@dataclass(frozen=True)
class FakeCatalog:
    """循环只调 to_model_schemas, 目录的其余部分与它无关."""

    names: tuple[str, ...]

    def to_model_schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(
            ToolSchema(name=name, description=f"fake {name}", parameters={})
            for name in self.names
        )


def prompt_snapshot() -> PromptSnapshot:
    return PromptSnapshot(
        blocks=(
            PromptBlock(
                block_id=PromptBlockId.CORE_IDENTITY,
                heading="identity",
                body="test double",
            ),
        )
    )


def user(text: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))


def assistant(text: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.ASSISTANT, content=(TextBlock(text),))


@dataclass(frozen=True)
class LoopInputSpec:
    tools: tuple[str, ...] = ("fs_read", "shell_run")
    window: tuple[ChatMessage, ...] = field(default_factory=lambda: (user("hi"),))
    budget: ContextBudget | None = None
    state_frame: str = ""


def loop_input(spec: LoopInputSpec | None = None) -> LoopInput:
    spec = spec or LoopInputSpec()
    return LoopInput(
        turn_id="turn_0001",
        session_id="sess_test",
        mode=SessionMode.AUTO,
        context=AssembledContext(
            policy=prompt_snapshot(),
            initial_window=spec.window,
            budget=spec.budget,
            state_frame=spec.state_frame,
        ),
        tool_catalog=FakeCatalog(spec.tools),  # type: ignore[arg-type]
    )


def call(
    name: str, tool_call_id: str = "c1", **arguments: object
) -> tuple[str, str, str]:
    """Reply.tool_calls 的一项."""
    return (tool_call_id, name, json.dumps(arguments))
