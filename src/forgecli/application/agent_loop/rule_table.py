"""规则表: 一份有序的规则列表, 以及在每个时机上怎么跑它 (ADR-0049 决策 3 / 决策 4).

只有两条执行规矩:

- 调模型之前, `Rewrite` 更新窗口之后**接着看下一条规则**, 后面的规则看到的是换过的窗口.
- 其余非 `Continue` 的处置, 后面的规则不再看, 交给循环立刻执行.

顺序就是列表的书写顺序. 硬约束由规则自己声明 (`runs_before` / `runs_after`), 装配时
校验, 挪错位置进程起不来. 不用数字, 不做排序: 十条规则四条约束, 排序会让剩下六条的
最终顺序变成一个要在脑子里跑一遍算法才知道的结果.

规则在任何时机抛异常, 记一条日志和一条运行事件, 按 `Continue` 处理, 本轮不因此失败
(ADR-0010 §7.2).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from forgecli.application.agent_loop.model_invoker import ModelOutcome
from forgecli.application.agent_loop.rule import (
    AfterModelVerdict,
    AfterObserveVerdict,
    BeforeDispatchVerdict,
    LoopRule,
    LoopView,
    ModelErrorVerdict,
)
from forgecli.application.agent_loop.run_events import LoopEventPublisher
from forgecli.application.agent_loop.verdicts import Continue, Rewrite
from forgecli.application.llm.gateway.errors import ModelGatewayError
from forgecli.domain.agent.actions import LoopObservation, LoopStop
from forgecli.domain.tool.tool_call import ToolCall
from forgecli.shared.observability.log import get_log

__all__ = ["RuleOrderError", "RuleTable", "validate_rule_order"]

_log = get_log(__name__)


class RuleOrderError(ValueError):
    """规则表的书写顺序违反了某条规则声明的硬约束."""


def validate_rule_order(rules: Sequence[LoopRule]) -> None:
    """按每条规则声明的约束检查列表顺序. 违反就抛, 消息里带两条规则的名字与理由.

    约束指向的规则不在表里也算错: 多半是改名之后忘了改声明, 而那种漏掉不会报错.
    """
    names = [rule.name for rule in rules]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise RuleOrderError(f"规则名重复: {sorted(duplicates)}")
    position = {name: index for index, name in enumerate(names)}
    for rule in rules:
        for constraint in rule.runs_before:
            peer = position.get(constraint.peer)
            if peer is None:
                raise RuleOrderError(
                    f"{rule.name} 声明要排在 {constraint.peer} 之前, 但表里没有它"
                )
            if position[rule.name] >= peer:
                raise RuleOrderError(
                    f"{rule.name} 必须排在 {constraint.peer} 之前: {constraint.why}"
                )
        for constraint in rule.runs_after:
            peer = position.get(constraint.peer)
            if peer is None:
                raise RuleOrderError(
                    f"{rule.name} 声明要排在 {constraint.peer} 之后, 但表里没有它"
                )
            if position[rule.name] <= peer:
                raise RuleOrderError(
                    f"{rule.name} 必须排在 {constraint.peer} 之后: {constraint.why}"
                )


class RuleTable:
    def __init__(
        self, rules: Sequence[LoopRule], *, events: LoopEventPublisher
    ) -> None:
        validate_rule_order(rules)
        self._rules = tuple(rules)
        self._events = events

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(rule.name for rule in self._rules)

    # ---- 五个时机 ----

    def before_model(
        self, view: Callable[[], LoopView], apply: Callable[[Rewrite], None]
    ) -> Continue | LoopStop:
        """`view` 每次现取: 前一条规则改写了窗口, 后一条要看到.

        改写由循环执行 (`apply`), 这里不碰循环的字段.
        """
        for rule in self._rules:
            verdict = self._guarded(
                rule, "before_model", lambda r: r.before_model(view())
            )
            if isinstance(verdict, Rewrite):
                apply(verdict)
            elif isinstance(verdict, LoopStop):
                return verdict
        return Continue()

    def on_model_error(
        self, error: ModelGatewayError, view: LoopView
    ) -> ModelErrorVerdict:
        for rule in self._rules:
            verdict = self._guarded(
                rule, "on_model_error", lambda r: r.on_model_error(error, view)
            )
            if not isinstance(verdict, Continue):
                return verdict
        return Continue()

    def after_model(self, outcome: ModelOutcome, view: LoopView) -> AfterModelVerdict:
        for rule in self._rules:
            verdict = self._guarded(
                rule, "after_model", lambda r: r.after_model(outcome, view)
            )
            if not isinstance(verdict, Continue):
                return verdict
        return Continue()

    def before_dispatch(self, call: ToolCall, view: LoopView) -> BeforeDispatchVerdict:
        for rule in self._rules:
            verdict = self._guarded(
                rule, "before_dispatch", lambda r: r.before_dispatch(call, view)
            )
            if not isinstance(verdict, Continue):
                return verdict
        return Continue()

    def after_observe(
        self, call: ToolCall, observation: LoopObservation, view: LoopView
    ) -> AfterObserveVerdict:
        for rule in self._rules:
            verdict = self._guarded(
                rule,
                "after_observe",
                lambda r: r.after_observe(call, observation, view),
            )
            if not isinstance(verdict, Continue):
                return verdict
        return Continue()

    # ---- 隔离 ----

    def _guarded[V](
        self, rule: LoopRule, timing: str, ask: Callable[[LoopRule], V]
    ) -> V | Continue:
        try:
            return ask(rule)
        except Exception as error:
            # 一条规则坏了不该让整轮失败, 但也不能静默: 日志与事件各记一条.
            _log.exception(
                "loop.rule_failed",
                rule=rule.name,
                timing=timing,
                error=type(error).__name__,
                message=str(error),
            )
            self._events.decision(f"规则 {rule.name} 在 {timing} 出错, 已跳过")
            return Continue()
