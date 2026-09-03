"""驱动 BuiltinAgentLoop 的共享替身.

只有 gateway 是脚本化的; 循环, 事件总线与观察回填都是真的. 放在 support 是因为
时间线用例与拒绝处理用例都要用同一套 —— 各写一份迟早会漂移.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_run.events import (
    AgentRunEventBus,
    AgentRunEventSubscriber,
)
from forgecli.application.context.window_manager import WindowManager
from forgecli.application.llm.catalog import InMemoryModelCatalog
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.metering import CostEstimator, UsageMeter
from forgecli.domain.agent.run_events import AgentRunEvent, AgentRunEventKind
from forgecli.domain.agent.state import LoopInput
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
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.spec import TargetDeclarationAbility, ToolSpec
from forgecli.domain.tool.tool_call import ToolCall
from support.fakes import assembled

__all__ = [
    "CATALOG",
    "Collector",
    "ScriptedGateway",
    "call",
    "loop_with",
    "loop_input",
    "request_prefix",
    "response",
]


def _spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1",
        title=name,
        description=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        declared_capabilities=frozenset({Capability.WORKSPACE_READ}),
        target_declaration_ability=TargetDeclarationAbility.STATIC,
        default_timeout_seconds=10.0,
    )


# 有目录才能断言"目录被收掉了" —— 一开始就是空的话, 那个断言恒真.
CATALOG = ToolCatalog(
    entries=(_spec("fs_read"), _spec("search_text"), _spec("shell_run")),
    reason_tag="test",
)


@dataclass
class ScriptedGateway(LlmGateway):
    responses: list[ModelResponse] = field(default_factory=list)
    # 每次调用时模型看到的工具数量; 用来断言"工具目录被收掉了".
    requests_tools: list[int] = field(default_factory=list)
    last_messages: tuple[ChatMessage, ...] = ()
    # 收下每一次请求的完整形状: 提示词是否一轮内保持同一份, 只能这样断言.
    requests: list[ModelRequest] = field(default_factory=list)

    def _record(self, request: ModelRequest) -> None:
        self.requests_tools.append(len(request.tools))
        self.last_messages = request.messages
        self.requests.append(request)

    def complete(self, request: ModelRequest) -> ModelResponse:
        self._record(request)
        return self.responses.pop(0)

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        """把脚本化响应拆成流式块.

        循环接了事件总线就会走流式路径, 所以替身也得支持它 —— 否则测的是一条生产里
        根本不走的分支.
        """
        self._record(request)
        response = self.responses.pop(0)
        sequence = 0

        def _chunk(**fields: object) -> ModelStreamChunk:
            nonlocal sequence
            chunk = ModelStreamChunk(
                request_id=request.request_id,
                sequence=sequence,
                provider=response.provider,
                model=response.model,
                **fields,  # type: ignore[arg-type]
            )
            sequence += 1
            return chunk

        if response.content:
            yield _chunk(delta_text=response.content)
        for index, call in enumerate(response.tool_calls):
            yield _chunk(
                tool_call_deltas=(
                    ToolCallDelta(
                        index=index,
                        tool_call_id=call.tool_call_id,
                        name=call.name,
                        arguments_delta=json.dumps(dict(call.arguments)),
                    ),
                )
            )
        yield _chunk(usage_delta=response.usage, finish_reason=response.finish_reason)

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        raise NotImplementedError


class Collector(AgentRunEventSubscriber):
    def __init__(self) -> None:
        self.events: list[AgentRunEvent] = []

    def on_event(self, event: AgentRunEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[AgentRunEventKind]:
        return [event.kind for event in self.events]

    def of(self, kind: AgentRunEventKind) -> list[AgentRunEvent]:
        return [event for event in self.events if event.kind is kind]


def response(text: str = "", tool_calls: tuple[ToolCall, ...] = ()) -> ModelResponse:
    return ModelResponse(
        request_id="req",
        provider="fake",
        model="fake-model",
        content=text,
        finish_reason=FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP,
        usage=ModelUsage(input_tokens=7, output_tokens=3),
        latency_ms=1.0,
        tool_calls=tool_calls,
    )


def loop_with(
    gateway: ScriptedGateway, *, context: WindowManager | None = None
) -> tuple[BuiltinAgentLoop, Collector]:
    bus = AgentRunEventBus()
    collector = Collector()
    bus.subscribe(collector)
    loop = BuiltinAgentLoop(
        gateway,
        UsageMeter(CostEstimator(InMemoryModelCatalog())),
        event_bus=bus,
        context=context,
    )
    return loop, collector


def loop_input(*, with_tools: bool = True, state_frame: str = "") -> LoopInput:
    catalog = CATALOG if with_tools else None
    return LoopInput(
        turn_id="turn-1",
        session_id="sess-1",
        mode=SessionMode.ACCEPT_EDITS,
        context=assembled(
            state_frame=state_frame,
            window=(ChatMessage(role=MessageRole.USER, content=(TextBlock("你好"),)),),
        ),
        tool_catalog=catalog,
    )


def request_prefix(request: ModelRequest) -> tuple[object, ...]:
    """一次请求里**应当逐字节稳定**的那一段 (ADR-0041 决策 1).

    工具目录 + system prompt + 除最后一条外的全部消息. 最后一条排除在外, 因为状态帧
    每轮重建, 它本来就该变 —— 把它算进来, 这个断言就永远不成立, 也就查不出真正的回归.
    """
    return (
        tuple(schema.name for schema in request.tools),
        request.system_prompt,
        tuple(_message_key(message) for message in request.messages[:-1]),
    )


def _message_key(message: ChatMessage) -> tuple[object, ...]:
    return (
        message.role.value,
        tuple(getattr(block, "text", repr(block)) for block in message.content),
        tuple(call.tool_call_id for call in message.tool_calls),
    )


def call(name: str, call_id: str) -> ToolCall:
    return ToolCall(
        tool_call_id=call_id, name=name, arguments=MappingProxyType({"path": "a.py"})
    )
