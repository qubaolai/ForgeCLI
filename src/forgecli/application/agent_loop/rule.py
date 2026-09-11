"""一条循环规则长什么样 (ADR-0049 决策 2 / 决策 3).

规则只回答"这一步该怎么处置", 机制留在循环里: 派工具, 补配对结果, 写 assistant 消息,
组请求, 这些规则都碰不到. 规则能看的是 `LoopView` 这份只读快照, 能写的只有它上面的
账本.

五个时机, 每个时机一套封闭的处置. 一条规则通常只覆写一两个方法, 其余用基类的默认.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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
    "AfterModelVerdict",
    "AfterObserveVerdict",
    "BeforeDispatchVerdict",
    "BeforeModelVerdict",
    "LoopRule",
    "LoopRuleBase",
    "LoopView",
    "ModelErrorVerdict",
    "OrderRule",
]

BeforeModelVerdict = Continue | Rewrite | LoopStop
ModelErrorVerdict = Continue | Reask | Rewrite | LoopStop
AfterModelVerdict = Continue | Replace | Reask | LoopStop
BeforeDispatchVerdict = Continue | Deny | LoopStop
AfterObserveVerdict = Continue | Inject | CloseTools | LoopStop


@dataclass(frozen=True)
class OrderRule:
    """一条硬顺序约束.

    `why` 必填: 写不出"违反了会怎么坏"的, 就不是硬约束, 是偏好. 偏好不声明, 由规则表
    的书写顺序表达就够. 这跟 check_arch.py 里 SIBLING_BANS 的 (源, 禁止, 理由) 是同一
    个形状.
    """

    peer: str
    why: str

    def __post_init__(self) -> None:
        if not self.peer.strip():
            raise ValueError("OrderRule.peer 不能为空")
        if not self.why.strip():
            raise ValueError("OrderRule.why 不能为空: 说不出理由的不是硬约束")


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


class LoopRule(Protocol):
    """内置规则. 由组合根显式装配, 顺序写死, 可以使用全套处置.

    将来项目, 用户或 Skill 要往循环上挂东西, 走另一个类型, 只允许继续与追加消息
    (ADR-0049 决策 5). 那个类型等第一个外部规则出现时再写.
    """

    # 三个都是只读属性而不是可写字段: 规则表只读它们, 而只读的写法让子类可以直接写
    # 类属性 (`runs_before = (OrderRule(...),)`), 不用逐个标注型.
    @property
    def name(self) -> str: ...

    @property
    def runs_before(self) -> tuple[OrderRule, ...]: ...

    @property
    def runs_after(self) -> tuple[OrderRule, ...]: ...

    def before_model(self, view: LoopView) -> BeforeModelVerdict: ...

    def on_model_error(
        self, error: ModelGatewayError, view: LoopView
    ) -> ModelErrorVerdict: ...

    def after_model(
        self, outcome: ModelOutcome, view: LoopView
    ) -> AfterModelVerdict: ...

    def before_dispatch(
        self, call: ToolCall, view: LoopView
    ) -> BeforeDispatchVerdict: ...

    def after_observe(
        self, call: ToolCall, observation: LoopObservation, view: LoopView
    ) -> AfterObserveVerdict: ...


class LoopRuleBase:
    """五个方法默认都是"没有意见". 子类只覆写它关心的那一两个, 并给 name."""

    name: str
    runs_before: tuple[OrderRule, ...] = ()
    runs_after: tuple[OrderRule, ...] = ()

    def before_model(self, view: LoopView) -> BeforeModelVerdict:
        return Continue()

    def on_model_error(
        self, error: ModelGatewayError, view: LoopView
    ) -> ModelErrorVerdict:
        return Continue()

    def after_model(self, outcome: ModelOutcome, view: LoopView) -> AfterModelVerdict:
        return Continue()

    def before_dispatch(self, call: ToolCall, view: LoopView) -> BeforeDispatchVerdict:
        return Continue()

    def after_observe(
        self, call: ToolCall, observation: LoopObservation, view: LoopView
    ) -> AfterObserveVerdict:
        return Continue()
