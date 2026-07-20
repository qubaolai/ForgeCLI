"""会话域的小型词汇枚举：消息角色与一轮 turn 的终态。

与 SessionMode 同列放 domain（无依赖），让 application 各处都能引用而不互相耦合：
SessionService 写事件用 MessageRole/TurnStatus，AgentTurnService 编排也用 TurnStatus，
但 SessionService 不必依赖 application/agent_turn（避免 session ↔ agent_turn 循环）。
为将来 sub agent 预留 MessageRole.SUB_AGENT（本次不启用）。
"""

from __future__ import annotations

from enum import Enum


class MessageRole(Enum):
    """消息角色。TOOL 用于工具结果回填消息（ADR-0011 §10）。"""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TurnStatus(Enum):
    """一轮 agent turn 的终态。"""

    COMPLETED = "completed"
    FAILED = "failed"
