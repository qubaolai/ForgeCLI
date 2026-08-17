"""流式增量的累积器（ADR-0011 §9）。

chunk 值对象（ToolCallDelta / ProviderStreamChunk / ModelStreamChunk）住在
domain.model.streaming；这里只留 StreamAccumulator——它持有可变的累积状态、解析
JSON、在参数不完整时抛 ModelResponseParseError，是运行时机制。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from forgecli.application.llm.gateway.errors import ModelResponseParseError
from forgecli.domain.model.response import FinishReason, ModelUsage
from forgecli.domain.model.streaming import ModelStreamChunk, ToolCallDelta
from forgecli.domain.tool.tool_call import ToolCall


@dataclass
class _PendingToolCall:
    tool_call_id: str = ""
    name: str = ""
    arguments_parts: list[str] = field(default_factory=list)


class StreamAccumulator:
    """把流式 chunk 合并成完整响应要素（供 gateway 收尾与 AgentTurn 消费）。

    tool call delta 按 index 聚合；`tool_calls()` 只在参数为完整合法 JSON 对象时
    产出 ToolCall，半截 delta 不产出（§9.1：只作 interrupted 诊断摘要）。
    """

    def __init__(self) -> None:
        self._text_parts: list[str] = []
        self._pending_tools: dict[int, _PendingToolCall] = {}
        self._usage: ModelUsage | None = None
        self._finish_reason: FinishReason | None = None

    def add(self, chunk: ModelStreamChunk) -> None:
        if chunk.delta_text:
            self._text_parts.append(chunk.delta_text)
        for delta in chunk.tool_call_deltas:
            self._add_tool_delta(delta)
        if chunk.usage_delta is not None:
            self._usage = chunk.usage_delta
        if chunk.finish_reason is not None:
            self._finish_reason = chunk.finish_reason

    def _add_tool_delta(self, delta: ToolCallDelta) -> None:
        pending = self._pending_tools.setdefault(delta.index, _PendingToolCall())
        if delta.tool_call_id:
            pending.tool_call_id = delta.tool_call_id
        if delta.name:
            pending.name = delta.name
        if delta.arguments_delta:
            pending.arguments_parts.append(delta.arguments_delta)

    @property
    def text(self) -> str:
        return "".join(self._text_parts)

    @property
    def usage(self) -> ModelUsage | None:
        return self._usage

    @property
    def finish_reason(self) -> FinishReason | None:
        return self._finish_reason

    def tool_calls(self) -> tuple[ToolCall, ...]:
        """归一化累积完成的工具调用；参数非完整合法 JSON 对象 -> parse error。"""
        calls: list[ToolCall] = []
        for index in sorted(self._pending_tools):
            pending = self._pending_tools[index]
            raw_arguments = "".join(pending.arguments_parts) or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ModelResponseParseError(
                    f"工具调用 {pending.name or index} 的参数不是完整合法 JSON：{exc}"
                ) from None
            if not isinstance(arguments, dict):
                raise ModelResponseParseError(
                    f"工具调用 {pending.name or index} 的参数必须是 JSON 对象"
                )
            calls.append(
                ToolCall(
                    tool_call_id=pending.tool_call_id or f"tool_call_{index}",
                    name=pending.name,
                    arguments=arguments,
                )
            )
        return tuple(calls)

    def has_partial_tool_calls(self) -> bool:
        """是否存在尚未能归一化的半截 tool call delta（中断诊断用）。"""
        for pending in self._pending_tools.values():
            raw_arguments = "".join(pending.arguments_parts) or "{}"
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError:
                return True
            if not isinstance(parsed, dict) or not pending.name:
                return True
        return False
