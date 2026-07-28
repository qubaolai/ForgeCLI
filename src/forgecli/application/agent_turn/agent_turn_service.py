"""AgentTurnService：一轮自然语言对话的 application 用例（ADR-0010 §3 / §5）。

2026-07-24 起本类驱动 AgentLoop（MVP 实现 BuiltinAgentLoop）：service 是唯一执行
副作用的 application service——事件成对落盘、usage 草稿写入、观察回填都在这里；
loop 只产出结构化意图（LoopDecision / LoopAction / LoopStop）。

约束（ADR-0003 / 概要设计 §6.5）：service 不依赖 Rich/Typer/prompt_toolkit；
**所有事件落盘只经 SessionService 单一门面**，本类不持有 EventStore/StateStore。

一轮 = 一个 turn：成对写 user_message / assistant_message，共享 turn_id；
turn 的终态写在 assistant_message 上（COMPLETED；异常与阻塞停止隔离为 FAILED）。
取消轮（Ctrl-C）落盘已收到的部分流式文本 + 取消标记，payload 带
stop_reason=user_cancelled，事件日志无歧义。

内存 transcript 镜像事件重放：user/assistant 成对文本全部进入历史（取消标记随
文本可见，错误轮的可行动文案也让模型知道上轮失败）。resume 时从历史事件回填。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from forgecli.application.agent_loop import AgentLoop
from forgecli.application.session import SessionService
from forgecli.domain.agent.actions import AnswerAction, LoopObservation, LoopStop
from forgecli.domain.agent.state import ContextPackage, LoopInput, ModePolicy
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import AssistantResponse, MessageRole, TurnStatus
from forgecli.domain.intents import SessionMode, UserMessage
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.session.events import EventType, SessionEvent

# 单轮驱动的安全步数上限：本切片只需 start + observe 两步，上限防御失控实现。
_MAX_LOOP_STEPS = 8

_CANCEL_NOTICE = "（本轮回复已被用户取消）"


@dataclass(frozen=True)
class _TurnOutcome:
    """一轮驱动的内部结果：文本 + 终态 + 待落盘 usage 草稿 + 机器可读停止标记。"""

    text: str
    status: TurnStatus
    usage_drafts: tuple[UsageRecordDraft, ...] = ()
    stop_reason: str | None = None


class AgentTurnService:
    """处理一轮自然语言输入：驱动 AgentLoop 并成对落盘会话事件。"""

    def __init__(
        self,
        session: SessionService,
        *,
        loop_factory: Callable[[], AgentLoop],
    ) -> None:
        self._session = session
        # 每 turn 经工厂取新 loop 实例（BuiltinAgentLoop 持有 per-turn 状态）。
        self._loop_factory = loop_factory
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
        outcome = self._obtain_outcome(text, mode, turn_id)
        self._session.record_assistant_message(
            outcome.text,
            turn_id=turn_id,
            status=outcome.status,
            stop_reason=outcome.stop_reason,
        )
        for draft in outcome.usage_drafts:
            # usage 写入边界（ADR-0011 §11.1）：loop 只随回复交回草稿，这里统一落盘。
            self._session.record_usage(draft.to_payload(), turn_id=turn_id)
        self._remember_turn(text, outcome)
        return AssistantResponse(
            turn_id=turn_id, text=outcome.text, status=outcome.status
        )

    # ---- 内部 ----

    def _obtain_outcome(
        self, text: str, mode: SessionMode, turn_id: str
    ) -> _TurnOutcome:
        try:
            return self._run_loop(text, mode, turn_id)
        except Exception:
            # 失败隔离：驱动抛错不破坏会话，仍成对落盘 assistant。
            return _TurnOutcome(text="助手处理出错。", status=TurnStatus.FAILED)

    def _run_loop(self, text: str, mode: SessionMode, turn_id: str) -> _TurnOutcome:
        loop = self._loop_factory()
        step = loop.start(
            LoopInput(
                turn_id=turn_id,
                session_id=self._session.current().session_id,
                user_intent=UserMessage(raw_text=text),
                mode=mode,
                mode_policy=ModePolicy(mode=mode),
                context_package=ContextPackage(
                    messages=(
                        *self._history,
                        ChatMessage(role=MessageRole.USER, content=(TextBlock(text),)),
                    )
                ),
            )
        )
        answer: str | None = None
        for _ in range(_MAX_LOOP_STEPS):
            if isinstance(step, LoopStop):
                return self._outcome_from_stop(step, answer, loop)
            action = step.next_action
            if isinstance(action, AnswerAction):
                answer = action.text
                step = loop.observe(
                    LoopObservation(content="answer_delivered", source="turn_service")
                )
            elif action is None:
                # 纯反思步：无动作可执行，直接回喂空观察继续。
                step = loop.observe(
                    LoopObservation(content="noop", source="turn_service")
                )
            else:
                # tool / ask_user / approval / compaction 属后续切片（07-30 起）。
                return _TurnOutcome(
                    text="该动作类型尚未支持（工具与审批属后续切片）。",
                    status=TurnStatus.FAILED,
                    usage_drafts=_drafts_of(loop),
                )
        return _TurnOutcome(
            text="循环步数超出安全上限，本轮中止。",
            status=TurnStatus.FAILED,
            usage_drafts=_drafts_of(loop),
        )

    def _outcome_from_stop(
        self, stop: LoopStop, answer: str | None, loop: AgentLoop
    ) -> _TurnOutcome:
        drafts = _drafts_of(loop)
        if stop.reason is LoopStopReason.FINAL_ANSWER:
            if answer is None:
                return _TurnOutcome(
                    text="循环未产出回复。",
                    status=TurnStatus.FAILED,
                    usage_drafts=drafts,
                    stop_reason=stop.reason.value,
                )
            return _TurnOutcome(
                text=answer,
                status=TurnStatus.COMPLETED,
                usage_drafts=drafts,
                stop_reason=stop.reason.value,
            )
        if stop.reason is LoopStopReason.USER_CANCELLED:
            partial = _partial_answer_of(loop)
            text = f"{partial}\n{_CANCEL_NOTICE}" if partial else _CANCEL_NOTICE
            return _TurnOutcome(
                text=text,
                status=TurnStatus.FAILED,
                usage_drafts=drafts,
                stop_reason=stop.reason.value,
            )
        return _TurnOutcome(
            text=stop.message or "模型调用失败。",
            status=TurnStatus.FAILED,
            usage_drafts=drafts,
            stop_reason=stop.reason.value,
        )

    def _remember_turn(self, text: str, outcome: _TurnOutcome) -> None:
        """把本轮写进内存 transcript：成对文本全部进入历史（镜像事件重放）。"""
        self._history.append(
            ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))
        )
        if outcome.text:
            self._history.append(
                ChatMessage(
                    role=MessageRole.ASSISTANT, content=(TextBlock(outcome.text),)
                )
            )


def _drafts_of(loop: AgentLoop) -> tuple[UsageRecordDraft, ...]:
    """读取 loop 交回的 usage 草稿（不在冻结 ABC 上，鸭子类型消费）。"""
    drafts = getattr(loop, "usage_drafts", ())
    return tuple(drafts)


def _partial_answer_of(loop: AgentLoop) -> str | None:
    """读取取消时已累积的部分回答（不在冻结 ABC 上，鸭子类型消费）。"""
    partial = getattr(loop, "partial_answer", None)
    return partial if isinstance(partial, str) and partial else None


def _rebuild_transcript(events: list[SessionEvent]) -> list[ChatMessage]:
    """从历史事件重建归一化 transcript（镜像重放全部成对 user/assistant 文本）。"""
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
            transcript.append(
                ChatMessage(role=MessageRole.ASSISTANT, content=(TextBlock(text),))
            )
    return transcript
