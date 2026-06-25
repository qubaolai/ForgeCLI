"""Agent turn 的返回 DTO。

AssistantResponse 是 handle_user_message 的结果：CLI 只拿它渲染助手那一轮，
不关心事件如何落盘（落盘在 service 内经 SessionService 完成）。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.conversation import TurnStatus


@dataclass(frozen=True)
class AssistantResponse:
    """一轮对话的助手结果：turn 标识 + 文本 + 终态。"""

    turn_id: str
    text: str
    status: TurnStatus
