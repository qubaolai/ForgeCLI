"""本轮的账本 (ADR-0049 决策 2).

用量草稿与压缩记录都记在这里. 它是规则在只读快照上唯一能写的东西, 也是规则把自己看见
的事实报出去的口子: 压缩发生了, 就在这里记, 事件由这里发 —— 规则拿不到事件发布器的
其他方法, 所以它编不出"本轮结束"这种它没看见的事实.

落盘仍由 AgentTurnService 做 (ADR-0037): 循环可以调模型, 但不写会话事件.
"""

from __future__ import annotations

from forgecli.application.agent_loop.run_events import LoopEventPublisher
from forgecli.application.context.window_manager import WindowFitResult
from forgecli.domain.context.compaction import CompactionDraft
from forgecli.domain.model.usage import UsageRecordDraft

__all__ = ["TurnLedger"]


class TurnLedger:
    def __init__(self, events: LoopEventPublisher) -> None:
        self._events = events
        self._usage: list[UsageRecordDraft] = []
        self._compactions: list[CompactionDraft] = []

    def add_usage(self, draft: UsageRecordDraft) -> None:
        """一次模型调用的账. 压缩那次调用也走这里 (ADR-0037), 分两份迟早两套单价."""
        self._usage.append(draft)

    def record_fit(self, result: WindowFitResult) -> None:
        """一次窗口整理: 记压缩记录与它花掉的用量, 并把这件事报出去."""
        self._compactions.extend(result.drafts)
        self._usage.extend(result.usage_drafts)
        self._events.compaction(result)

    @property
    def usage_drafts(self) -> tuple[UsageRecordDraft, ...]:
        return tuple(self._usage)

    @property
    def compaction_drafts(self) -> tuple[CompactionDraft, ...]:
        return tuple(self._compactions)
