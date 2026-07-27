"""对话的领域词汇: 角色, turn 终态, 助手结果, 消息内容块。"""

from forgecli.domain.conversation.message import (
    ChatMessage,
    ContentBlock,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.conversation.turn import (
    AssistantResponse,
    MessageRole,
    TurnStatus,
)

__all__ = [
    "AssistantResponse",
    "ChatMessage",
    "ContentBlock",
    "MessageRole",
    "TextBlock",
    "ToolResultBlock",
    "TurnStatus",
]
