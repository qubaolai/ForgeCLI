"""AgentTurnService：一轮自然语言对话的 application 用例。

把"取回复 + 成对落盘事件"收敛到这里。回复来源两条路径：
    - replier（TurnReplier，ADR-0011 07-07 集成切片）：真实实现经 LlmGateway 完成
      chat turn，回复随附 usage 计量草稿，由本类经 SessionService 写入 usage 事件
      （gateway 不直接落盘，§8 / §11.1）。
    - reply 回调（旧 stub 路径）：未接 replier 时的占位回复，保持既有测试与
      未配置模型时的可用性。

约束（ADR-0003 / 概要设计 §6.5）：service 不依赖 Rich/Typer/prompt_toolkit；
**所有事件落盘只经 SessionService 单一门面**，本类不持有 EventStore/StateStore。

一轮 = 一个 turn：成对写 user_message / assistant_message，共享 turn_id；
turn 的终态写在 assistant_message 上（COMPLETED；取回复抛错则隔离为 FAILED）。
本类还维护内存 transcript（归一化 ChatMessage 历史），供 gateway 调用携带多轮
上下文；resume 时从历史事件回填。上下文压缩（compact）属后续切片。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from forgecli.application.agent_turn.replier import TurnReplier, TurnReply
from forgecli.application.agent_turn.turn import AssistantResponse
from forgecli.application.llm.gateway.messages import ChatMessage, TextBlock
from forgecli.application.session import EventType, SessionEvent, SessionService
from forgecli.domain.conversation import MessageRole, TurnStatus
from forgecli.domain.intents import SessionMode


def _stub_reply(text: str, mode: SessionMode) -> str:
    """占位助手回复（未接 LlmGateway 或未配置模型时）。"""
    return f"(LLM 接入开发中 · {mode.value} 模式)已收到你的消息。"


class AgentTurnService:
    """处理一轮自然语言输入，返回助手响应并成对落盘会话事件。"""

    def __init__(
        self,
        session: SessionService,
        *,
        reply: Callable[[str, SessionMode], str] = _stub_reply,
        replier: TurnReplier | None = None,
    ) -> None:
        self._session = session
        self._reply = reply
        self._replier = replier
        self._turns = 0
        self._history: list[ChatMessage] = []

    def resume(self, history: Iterable[SessionEvent]) -> None:
        """续写一段历史会话：接回 turn 计数，并从事件回填内存 transcript。"""
        events = list(history)
        self._turns = sum(1 for e in events if e.type == EventType.USER_MESSAGE)
        self._history = _rebuild_transcript(events)

    def handle_user_message(self, text: str) -> AssistantResponse:
        self._turns += 1
        turn_id = f"turn_{self._turns:04d}"
        self._session.record_user_message(text, turn_id=turn_id)
        # mode 从 session 快照读，单一真相（不再依赖 REPL 内存态）。
        mode = self._session.current().mode
        result = self._obtain_reply(text, mode, turn_id)
        self._session.record_assistant_message(
            result.text, turn_id=turn_id, status=result.status
        )
        if result.usage is not None:
            # usage 写入边界（§11.1）：gateway 只随回复交回草稿，这里统一落盘。
            self._session.record_usage(result.usage.to_payload(), turn_id=turn_id)
        self._remember_turn(text, result)
        return AssistantResponse(
            turn_id=turn_id, text=result.text, status=result.status
        )

    # ---- 内部 ----

    def _obtain_reply(self, text: str, mode: SessionMode, turn_id: str) -> TurnReply:
        try:
            if self._replier is not None:
                return self._replier.reply(
                    text=text,
                    mode=mode,
                    session_id=self._session.current().session_id,
                    turn_id=turn_id,
                    history=tuple(self._history),
                )
            body = self._reply(text, mode)
            return TurnReply(text=body, status=TurnStatus.COMPLETED)
        except Exception:
            # 失败隔离：取回复抛错不破坏会话，仍成对落盘 assistant。
            return TurnReply(text="助手处理出错（stub）。", status=TurnStatus.FAILED)

    def _remember_turn(self, text: str, result: TurnReply) -> None:
        """把本轮写进内存 transcript；失败回复不进上下文（避免污染后续调用）。"""
        self._history.append(
            ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))
        )
        if result.status is TurnStatus.COMPLETED and result.text:
            self._history.append(
                ChatMessage(
                    role=MessageRole.ASSISTANT, content=(TextBlock(result.text),)
                )
            )


def _rebuild_transcript(events: list[SessionEvent]) -> list[ChatMessage]:
    """从历史事件重建归一化 transcript（只取成对的 user/assistant 文本）。"""
    transcript: list[ChatMessage] = []
    for event in events:
        text = str(event.payload.get("text", ""))
        if not text:
            continue
        if event.type == EventType.USER_MESSAGE:
            transcript.append(
                ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))
            )
        elif event.type == EventType.ASSISTANT_MESSAGE:
            status = str(event.payload.get("status", TurnStatus.COMPLETED.value))
            if status == TurnStatus.COMPLETED.value:
                transcript.append(
                    ChatMessage(role=MessageRole.ASSISTANT, content=(TextBlock(text),))
                )
    return transcript
