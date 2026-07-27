"""工具调用词汇: ToolSpec 与 ToolCall (ADR-0011 §10)。

从 conversation/message 拆出来单独成包: 工具是与"对话"并列的一等概念, 07-30 起
ToolRegistry / ToolRuntime 都要引用 ToolSpec, 它不该继续挂在消息模块下。

tool calling 接线整体推迟, 但字段位先冻, 让 ModelRequest.tools 与
ModelResponse.tool_calls 不必等到工具切片才定型。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = ["ToolCall", "ToolSpec"]


@dataclass(frozen=True)
class ToolSpec:
    """ForgeCLI 工具规格（占位，tool calling 接线推迟，字段位先冻）。

    parameters 为工具入参的 JSON schema；provider adapter 负责转成各供应商 tool schema。
    """

    name: str
    description: str
    parameters: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ToolSpec.name 不能为空")


@dataclass(frozen=True)
class ToolCall:
    """模型返回的工具调用意图（占位，字段位先冻）。

    tool_call_id 用于把 tool call 与后续 tool result 关联（§10）。
    arguments 为未执行的原始入参；执行前仍须经 schema 校验，不可直接信任。
    """

    tool_call_id: str
    name: str
    arguments: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.tool_call_id.strip():
            raise ValueError("ToolCall.tool_call_id 不能为空")
        if not self.name.strip():
            raise ValueError("ToolCall.name 不能为空")
