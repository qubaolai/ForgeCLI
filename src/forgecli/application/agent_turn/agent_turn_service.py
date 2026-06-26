"""AgentTurnService: 一轮自然语言对话的 application 用例(当前为 占用)。

把原先散在 REPL 的"回显 + 拼 assistant 文本 + 记录事件"收敛到这里，给将来接
AgentWorkflow / LLM / 工具 / 子 agent 留稳定边界。本切片不接真实 LLM。

约束（ADR-0003 / 概要设计 §6.5）：service 不依赖 Rich/Typer/prompt_toolkit;
所有事件落盘只经 SessionService 单一门面，本类不持有 EventStore/StateStore。

一轮 = 一个 turn: 成对写 user_message / assistant_message, 共享 turn_id;
turn 的终态写在 assistant_message 上(COMPLETED; reply 抛错则隔离为 FAILED)。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from forgecli.application.agent_turn.turn import AssistantResponse
from forgecli.application.session.events import EventType, SessionEvent
from forgecli.application.session.session_service import SessionService
from forgecli.domain.conversation import TurnStatus
from forgecli.domain.intents import SessionMode


def _stub_reply(text: str, mode: SessionMode) -> str:
    """占位助手回复（真实 LLM 接入前）。带上当前模式，便于观察 mode 已生效。"""
    return f"(LLM 接入开发中 · {mode.value} 模式)已收到你的消息。"


class AgentTurnService:
    """处理一轮自然语言输入，返回助手响应并成对落盘会话事件。"""

    def __init__(
        self,
        session: SessionService,
        *,
        reply: Callable[[str, SessionMode], str] = _stub_reply,
    ) -> None:
        self._session = session
        self._reply = reply
        self._turns = 0

    def resume(self, history: Iterable[SessionEvent]) -> None:
        """续写一段历史会话：把 turn 计数接到历史已有的 user_message 数上，

        使下一轮 turn_id 接着往后排（与 SessionService.resume 配套使用）。
        """
        self._turns = sum(1 for e in history if e.type == EventType.USER_MESSAGE)

    def handle_user_message(self, text: str) -> AssistantResponse:
        self._turns += 1
        turn_id = f"turn_{self._turns:04d}"
        self._session.record_user_message(text=text, turn_id=turn_id)
        mode = self._session.current().mode
        try:
            body = self._reply(text, mode)
            status = TurnStatus.COMPLETED
        except Exception:
            # 失败隔离：reply（将来的 LLM 调用）抛错不破坏会话，仍成对落盘 assistant。
            body = "助手处理出错(stub)"
            status = TurnStatus.FAILED
        self._session.record_assistant_message(
            text=body, turn_id=turn_id, status=status
        )
        return AssistantResponse(turn_id=turn_id, text=body, status=status)
