"""一轮对话回复的端口 TurnReplier（ADR-0011 §8 / 2026-07-07 集成切片）。

AgentTurnService 通过本端口取得助手回复：真实实现走 LlmGateway
（gateway_replier.GatewayReplier），测试可注入任意替身。端口返回 TurnReply
而非裸文本，让 usage 计量草稿能随回复交回 AgentTurnService 统一落盘
（gateway 不直接写事件 / usage 文件）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from forgecli.application.llm.gateway.messages import ChatMessage
from forgecli.application.llm.metering import UsageRecordDraft
from forgecli.domain.conversation import TurnStatus
from forgecli.domain.intents import SessionMode


@dataclass(frozen=True)
class TurnReply:
    """一轮回复：文本 + 终态 + 待落盘 usage 草稿（可空）。"""

    text: str
    status: TurnStatus
    usage: UsageRecordDraft | None = None


class TurnReplier(ABC):
    """按当前 turn 上下文产出助手回复的端口。"""

    @abstractmethod
    def reply(
        self,
        *,
        text: str,
        mode: SessionMode,
        session_id: str,
        turn_id: str,
        history: tuple[ChatMessage, ...],
    ) -> TurnReply:
        """产出一轮回复。history 为本轮之前的归一化对话消息（不含本次输入）。"""
