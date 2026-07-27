"""一轮对话的词汇: 消息角色, turn 终态, 以及助手结果。

三者放在一起是因为它们互相定义: AssistantResponse 就是"一轮 turn 的文本 + 终态",
拆开只会让 TurnStatus 与它唯一的载体分居两处。

SessionService 写事件用 MessageRole/TurnStatus，AgentTurnService 编排也用 TurnStatus，
但 SessionService 不必依赖 application/agent_turn（避免 session ↔ agent_turn 循环）。
为将来 sub agent 预留 MessageRole.SUB_AGENT（本次不启用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["AssistantResponse", "MessageRole", "TurnStatus"]


class MessageRole(Enum):
    """消息角色。TOOL 用于工具结果回填消息（ADR-0011 §10）。"""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TurnStatus(Enum):
    """一轮 agent turn 的终态。"""

    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class AssistantResponse:
    """一轮对话的助手结果：turn 标识 + 文本 + 终态。

    handle_user_message 的返回值：CLI 只拿它渲染助手那一轮，不关心事件如何落盘
    （落盘在 service 内经 SessionService 完成）。
    """

    turn_id: str
    text: str
    status: TurnStatus
