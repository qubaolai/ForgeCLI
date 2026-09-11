"""规则能看到什么, 每个时机允许它返回什么 (ADR-0049 决策 2, 2026-09-11 修订).

一条规则就是一个方法: 收到只读快照 (加上这个时机的输入), 返回这个时机允许的处置之一.
方法叫什么名字随意, 属于哪个类随意 —— 它在哪个时机跑, 由 ``rules/__init__.py`` 里按时机
列的表决定, 不由方法名决定.

规则拿不到循环的字段, 能写的只有快照上的账本.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from forgecli.application.agent_loop.ledger import TurnLedger
from forgecli.application.agent_loop.model_invoker import ModelOutcome
from forgecli.application.agent_loop.verdicts import (
    CloseTools,
    Continue,
    Deny,
    Inject,
    Reask,
    Replace,
    Rewrite,
)
from forgecli.application.llm.gateway.errors import ModelGatewayError
from forgecli.domain.agent.actions import LoopObservation, LoopStop
from forgecli.domain.agent.phase import LoopPhase
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.context.window import Window
from forgecli.domain.intents import SessionMode
from forgecli.domain.tool.tool_call import ToolCall, ToolSchema

__all__ = [
    "AfterModelStep",
    "AfterModelVerdict",
    "AfterObserveStep",
    "AfterObserveVerdict",
    "BeforeDispatchStep",
    "BeforeDispatchVerdict",
    "BeforeModelStep",
    "BeforeModelVerdict",
    "LoopView",
    "ModelErrorStep",
    "ModelErrorVerdict",
]

# ---- 每个时机允许的处置 ----

BeforeModelVerdict = Continue | Rewrite | LoopStop
ModelErrorVerdict = Continue | Reask | Rewrite | LoopStop
AfterModelVerdict = Continue | Replace | Reask | LoopStop
BeforeDispatchVerdict = Continue | Deny | LoopStop
AfterObserveVerdict = Continue | Inject | CloseTools | LoopStop


@dataclass(frozen=True)
class LoopView:
    """规则能看到的东西. 全部只读, 除了 ledger."""

    session_id: str
    turn_id: str
    mode: SessionMode
    phase: LoopPhase
    window: Window
    tools: tuple[ToolSchema, ...]
    tools_closed: bool
    budget: ContextBudget | None
    system_prompt: str
    model_calls: int
    tool_calls: int
    # 只给数量, 不给队列: 规则改不了派发顺序.
    pending_calls: int
    ledger: TurnLedger


# ---- 每个时机上一条规则的形状 ----
#
# 把 `repeat.before_dispatch` 塞进 after_model 那一列, 或者让一个方法返回这个时机不允许
# 的处置, mypy 在装配那一行就报错.

BeforeModelStep = Callable[[LoopView], BeforeModelVerdict]
ModelErrorStep = Callable[[ModelGatewayError, LoopView], ModelErrorVerdict]
AfterModelStep = Callable[[ModelOutcome, LoopView], AfterModelVerdict]
BeforeDispatchStep = Callable[[ToolCall, LoopView], BeforeDispatchVerdict]
AfterObserveStep = Callable[[ToolCall, LoopObservation, LoopView], AfterObserveVerdict]
