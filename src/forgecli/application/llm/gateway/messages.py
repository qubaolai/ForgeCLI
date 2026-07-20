"""ForgeCLI 自有 message / tool 内容（ADR-0011 §3.3 / §10）。

message 不直接暴露第三方 SDK 对象；content 采用内容块数组：
    - TextBlock 为 MVP 必需。
    - image / file 块为预留扩展点（多模态，MVP 不实现，§1 范围）。

tools 使用 ForgeCLI ToolSpec，由 provider adapter 转成各供应商 tool schema。
tool calling 接线整体推迟（§10），但 ToolSpec / ToolCall 字段位今日先冻，
让 ModelRequest.tools 与 ModelResponse.tool_calls 不必等到工具切片才定型。

复用 domain.conversation.MessageRole（USER / ASSISTANT），保持角色词汇单一真相；
system 提示走 ModelRequest.system_prompt 独立字段，不占消息角色；tool 结果消息随
tool calling 切片再引入。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.domain.conversation import MessageRole


@dataclass(frozen=True)
class ContentBlock:
    """消息内容块密封基类。MVP 只实现 TextBlock；image/file 为预留扩展点。"""


@dataclass(frozen=True)
class TextBlock(ContentBlock):
    """纯文本内容块。"""

    text: str


@dataclass(frozen=True)
class ToolResultBlock(ContentBlock):
    """工具结果内容块（§10 工具结果回填）。

    统一的 tool result message（role=TOOL + 本块）表达工具结果；
    provider adapter 负责翻译成各供应商表示（如 OpenAI role=tool 消息）。
    tool_call_id 与触发它的 ToolCall 关联。
    """

    tool_call_id: str
    content: str
    is_error: bool = False

    def __post_init__(self) -> None:
        if not self.tool_call_id.strip():
            raise ValueError("ToolResultBlock.tool_call_id 不能为空")


@dataclass(frozen=True)
class ChatMessage:
    """一条对话消息：角色 + 内容块数组。"""

    role: MessageRole
    content: tuple[ContentBlock, ...]
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("ChatMessage.content 不能为空")


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
