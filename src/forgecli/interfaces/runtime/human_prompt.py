"""阻塞式人机提示 broker: Web 与终端共用一条待答队列 (ADR-0043 决策 3).

它顶替了原先的 ``BlockingApprovalBroker``. 那个类只服务审批, 而 ADR-0045 已经把它从
``interfaces/web/`` 上移到这里让两个界面共用 —— "等人回话只该有一份实现"这条判断在审批
上兑现过一次, 本模块把 ``ask_user`` 也收进同一条队列.

**它不认识审批, 也不认识提问.** 队列里躺的是 ``HumanPrompt``, 交回去的是
``PromptAnswer``; 唯一的校验是"这个选项是不是这条提示自己摆出来的"
(``HumanPrompt.accepts_answer``). 至于 ``once`` 意味着一档授权范围, 那是
``ApprovalService`` 的事, 而它给出的响应还要再过
``ApprovalRequest.response_error`` 那道闸 (见 ``coordinator.py``).

**不设等待超时**, 理由见 ``application/human_prompt.py``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from forgecli.application.human_prompt import (
    MAX_QUESTIONS_PER_TURN,
    HumanPromptService,
)
from forgecli.domain.human_prompt import HumanPrompt, PromptAnswer, PromptKind

__all__ = ["BlockingHumanPromptBroker"]


@dataclass
class _Pending:
    prompt: HumanPrompt
    ready: threading.Event = field(default_factory=threading.Event)
    answer: PromptAnswer | None = None
    release_note: str = ""


class BlockingHumanPromptBroker(HumanPromptService):
    """一个 ``prompt_id`` 只接受一次作答; 没拿到作答一律 ``resolved=False``."""

    def __init__(self, on_change: Callable[[str, str], None] | None = None) -> None:
        self._on_change = on_change
        self._lock = threading.RLock()
        self._pending: dict[str, _Pending] = {}
        self._questions_this_turn = 0
        self._closed = False

    # ---- 调用者一侧 ----

    def ask(self, prompt: HumanPrompt) -> PromptAnswer:
        pending = _Pending(prompt)
        with self._lock:
            refusal = self._refuse(prompt)
            if refusal is not None:
                return refusal
            self._pending[prompt.prompt_id] = pending
        try:
            self._notify(prompt.prompt_id, "prompt_requested")
            pending.ready.wait()
        finally:
            with self._lock:
                self._pending.pop(prompt.prompt_id, None)
            self._notify(prompt.prompt_id, "prompt_resolved")
        if pending.answer is not None:
            return pending.answer
        return PromptAnswer(
            prompt_id=prompt.prompt_id,
            resolved=False,
            note=pending.release_note or "没有拿到回答",
        )

    def _notify(self, prompt_id: str, kind: str) -> None:
        if self._on_change is not None:
            self._on_change(prompt_id, kind)

    def _refuse(self, prompt: HumanPrompt) -> PromptAnswer | None:
        """不必挂进队列就能答复的两种情况.

        进程正在退出时仍然挂进去, 等到的只会是 ``close`` 那一次释放 —— 中间白等一轮
        调度, 而结果一模一样.
        """
        with self._lock:
            if self._closed:
                return PromptAnswer(
                    prompt_id=prompt.prompt_id,
                    resolved=False,
                    note="Forge 正在退出, 没有人能回答",
                )
            if prompt.kind is PromptKind.QUESTION:
                if self._questions_this_turn >= MAX_QUESTIONS_PER_TURN:
                    return PromptAnswer(
                        prompt_id=prompt.prompt_id,
                        resolved=False,
                        note=(
                            f"本轮提问已达上限 {MAX_QUESTIONS_PER_TURN} 次, "
                            "请按你自己的判断继续"
                        ),
                    )
                self._questions_this_turn += 1
        return None

    # ---- 界面一侧 ----

    def list_pending(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            items = tuple(
                item for item in self._pending.values() if not item.ready.is_set()
            )
        return tuple(item.prompt.to_payload() for item in items)

    def find(self, prompt_id: str) -> HumanPrompt | None:
        """取一条还在等待中的提示. 作答之前先看它是什么, 决定要不要记一条会话事件."""
        with self._lock:
            pending = self._pending.get(prompt_id)
        return None if pending is None else pending.prompt

    def resolve(
        self,
        prompt_id: str,
        choice: str = "",
        text: str = "",
        *,
        selected_values: tuple[str, ...] = (),
        skipped: bool = False,
        before_resolve: Callable[[HumanPrompt, PromptAnswer], None] | None = None,
    ) -> bool:
        """记下一个人的作答. 返回 False 表示这条提示不在等待中, 或这个作答不合法.

        校验使用提示声明的选择模式、可用选项、自由文本与跳过规则:

        - ``choice`` 必须是它摆出来过的选项 (空串只在收自由文本时合法).
        - 不收自由文本的提示不接受 ``text``. 静默丢掉的话, 用户以为自己写下的理由被
          记下来了, 而实际上它从来没有离开过浏览器.
        """
        with self._lock:
            pending = self._pending.get(prompt_id)
            if pending is None or pending.ready.is_set():
                return False
            answer = PromptAnswer(
                prompt_id=prompt_id,
                choice=choice,
                text=text,
                resolved=True,
                selected_values=selected_values,
                skipped=skipped,
            )
            if not pending.prompt.accepts_answer(answer):
                return False
            if before_resolve is not None:
                before_resolve(pending.prompt, answer)
            pending.answer = answer
            pending.ready.set()
            return True

    # ---- 生命周期 ----

    def begin_turn(self) -> None:
        with self._lock:
            self._questions_this_turn = 0

    def release_pending(self, note: str) -> None:
        with self._lock:
            for item in self._pending.values():
                item.release_note = note
                item.ready.set()

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.release_pending("Forge 正在退出, 未收到回答")
