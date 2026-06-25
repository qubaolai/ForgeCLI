"""会话领域的枚举: 消息角色与一轮 turn 的状态"""

from __future__ import annotations

from enum import Enum


class MessageRole(Enum):
    """消息角色。当前只有 USER / ASSISTANT

    后续v2版本引入sub-agent 多agent模型 可以再扩展
    """

    USER = "user"
    ASSISTANT = "assistant"


class TurnStatus(Enum):
    """一轮 agent turn 的终态"""

    COMPLETED = "completed"
    FAILED = "failed"
