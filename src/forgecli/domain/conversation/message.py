"""ForgeCLI 自有 message 内容（ADR-0011 §3.3 / §10）。

message 不直接暴露第三方 SDK 对象；content 采用内容块数组：
    - TextBlock 为 MVP 必需。
    - image / file 块为预留扩展点（多模态，MVP 不实现，§1 范围）。

复用同包的 MessageRole（USER / ASSISTANT），保持角色词汇单一真相；
system 提示走 ModelRequest.system_prompt 独立字段，不占消息角色。

ToolSpec / ToolCall 住在 domain.tool: 工具是与对话并列的一等概念, 本模块只在
ChatMessage.tool_calls 与 ToolResultBlock 上引用它。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.tool.result import ResultProvenance
from forgecli.domain.tool.tool_call import ToolCall

__all__ = ["ChatMessage", "ContentBlock", "TextBlock", "ToolResultBlock"]


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
    # 这条结果的来源身份与归档位置 (ADR-0032). 上下文管理据此判断能不能去重, 以及
    # 降级之后取不取得回来. None 表示工具没声明, 那样的块原样留着.
    #
    # 放在块上而不是另建一张边表: 边表要靠索引对齐消息序列, 而消息序列在压缩过程中
    # 正在被重写 —— 一次没对齐就是把 A 的来源安到 B 头上, 且不会报错.
    provenance: ResultProvenance | None = None

    def __post_init__(self) -> None:
        if not self.tool_call_id.strip():
            raise ValueError("ToolResultBlock.tool_call_id 不能为空")


@dataclass(frozen=True)
class ChatMessage:
    """一条对话消息：角色 + 内容块数组。

    content 与 tool_calls 至少有一个非空：模型只决定调工具、不带任何文本时，
    assistant 消息的 content 为空、tool_calls 非空（§10 tool call 回填必须能把
    这条消息放回 transcript，否则 tool result 关联不上触发它的 tool call）。
    """

    role: MessageRole
    content: tuple[ContentBlock, ...]
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        if not self.content and not self.tool_calls:
            raise ValueError("ChatMessage 的 content 与 tool_calls 不能同时为空")
