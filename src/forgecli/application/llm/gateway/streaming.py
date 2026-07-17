"""统一流式 chunk 与合并器（ADR-0011 §9）。

CLI 渲染只消费 gateway 输出的 `ModelStreamChunk`；provider adapter 负责把供应商
私有 SSE/event 转成 `ProviderStreamChunk`。规则（§9）：

    - 最后一块必须包含或触发完整 ModelResponse 汇总（usage_delta + finish_reason）。
    - tool_call_delta 先进入内存 accumulator；只有参数完整且 JSON 解析通过后，
      才生成 ForgeCLI 归一化后的 ToolCall。
    - stream 中断 / 取消时，gateway 产出 interrupted=True 的收尾 chunk，
      finish_reason=user_cancelled（用户取消时），usage 为估算值。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from forgecli.application.llm.gateway.errors import ModelResponseParseError
from forgecli.application.llm.gateway.messages import ToolCall
from forgecli.application.llm.gateway.response import FinishReason, ModelUsage


@dataclass(frozen=True)
class ToolCallDelta:
    """一段工具调用增量（供应商流式返回的 tool call 片段）。

    index 标识同一响应内的第几个工具调用；tool_call_id / name 只在首个片段出现，
    arguments_delta 为 JSON 文本增量，累积完整后才解析。
    """

    index: int
    tool_call_id: str | None = None
    name: str | None = None
    arguments_delta: str = ""

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("ToolCallDelta.index 不能为负")


@dataclass(frozen=True)
class ProviderStreamChunk:
    """adapter 归一化后的供应商流式片段；gateway 再补 request_id / 序号。"""

    delta_text: str | None = None
    tool_call_delta: ToolCallDelta | None = None
    usage: ModelUsage | None = None
    finish_reason: FinishReason | None = None


@dataclass(frozen=True)
class ModelStreamChunk:
    """gateway 输出的统一流式片段（§9）。sequence 从 0 递增。"""

    request_id: str
    sequence: int
    delta_text: str | None = None
    tool_call_delta: ToolCallDelta | None = None
    usage_delta: ModelUsage | None = None
    finish_reason: FinishReason | None = None
    interrupted: bool = False

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("ModelStreamChunk.request_id 不能为空")
        if self.sequence < 0:
            raise ValueError("ModelStreamChunk.sequence 不能为负")


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
        if chunk.tool_call_delta is not None:
            self._add_tool_delta(chunk.tool_call_delta)
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
