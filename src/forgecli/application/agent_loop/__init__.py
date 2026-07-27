"""Agent 主循环切片（ADR-0010）：受控 ReAct 内核 `AgentLoop` 的契约与扩展机制。

本切片是 application 下与 agent_turn 并列的编排内核层。AgentLoop 只产出结构化意图
（LoopDecision / LoopAction / LoopStop），副作用一律由 AgentTurnService 执行。MVP
唯一实现是 BuiltinAgentLoop（2026-07-24 起落地）；不预先抽象 LoopAdapter 之类的编排
适配层（ADR-0010 §3）。

2026-07-23：只冻契约（输入 / 状态 / 三类产出 / 停止原因 / hooks / events 骨架）。
2026-07-24：落地 BuiltinAgentLoop（单步：一次模型调用 -> AnswerAction ->
FINAL_ANSWER）。

循环的词汇 (动作 / 状态 / 停止原因) 住在 domain.agent, 本包只暴露编排侧的实现与
扩展点, 不转手再导出领域类型。
"""

from __future__ import annotations

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_loop.events import (
    LoopEvent,
    LoopEventBus,
    LoopEventKind,
    LoopEventSubscriber,
)
from forgecli.application.agent_loop.hooks import HookResult, LoopHook
from forgecli.application.agent_loop.loop import AgentLoop

__all__ = [
    "AgentLoop",
    "BuiltinAgentLoop",
    "HookResult",
    "LoopEvent",
    "LoopEventBus",
    "LoopEventKind",
    "LoopEventSubscriber",
    "LoopHook",
]
