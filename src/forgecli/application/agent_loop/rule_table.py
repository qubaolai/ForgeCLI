"""规则表: 五个时机, 每个时机一列方法; 以及怎么跑它 (ADR-0049 决策 3, 2026-09-11 修订).

``RuleTable`` 是数据: 五个有序元组, 就是 ``rules/__init__.py`` 里写的那份. 读它就知道
每个时机跑什么, 什么顺序. 顺序的理由写在那份表旁边的注释里, 由
``tests/agent_loop/test_rule_table.py`` 守着 —— 改错顺序 ``make test`` 停.

``RuleRunner`` 是执行: 只有两条规矩.

- 调模型之前, ``Rewrite`` 更新窗口之后**接着看下一条**, 后面的看到的是换过的窗口.
- 其余非 ``Continue`` 的处置, 后面的不再看, 交给循环立刻执行.

一条规则抛异常, 记一条日志和一条运行事件, 按 ``Continue`` 处理, 本轮不因此失败
(ADR-0010 §7.2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from forgecli.application.agent_loop.model_invoker import ModelOutcome
from forgecli.application.agent_loop.rule import (
    AfterModelStep,
    AfterModelVerdict,
    AfterObserveStep,
    AfterObserveVerdict,
    BeforeDispatchStep,
    BeforeDispatchVerdict,
    BeforeModelStep,
    LoopView,
    ModelErrorStep,
    ModelErrorVerdict,
)
from forgecli.application.agent_loop.run_events import LoopEventPublisher
from forgecli.application.agent_loop.verdicts import Continue, Rewrite
from forgecli.application.llm.gateway.errors import ModelGatewayError
from forgecli.domain.agent.actions import LoopObservation, LoopStop
from forgecli.domain.tool.tool_call import ToolCall
from forgecli.shared.observability.log import get_log

__all__ = ["RuleRunner", "RuleTable"]

_log = get_log(__name__)


@dataclass(frozen=True)
class RuleTable:
    """五个时机各一列. 同一个对象的方法出现在几列, 它就参与几个时机, 状态自然共享."""

    before_model: tuple[BeforeModelStep, ...] = ()
    on_model_error: tuple[ModelErrorStep, ...] = ()
    after_model: tuple[AfterModelStep, ...] = ()
    before_dispatch: tuple[BeforeDispatchStep, ...] = ()
    after_observe: tuple[AfterObserveStep, ...] = ()

    def describe(self) -> dict[str, list[str]]:
        """每个时机跑哪些方法, 给日志用."""
        return {
            "before_model": [_name(step) for step in self.before_model],
            "on_model_error": [_name(step) for step in self.on_model_error],
            "after_model": [_name(step) for step in self.after_model],
            "before_dispatch": [_name(step) for step in self.before_dispatch],
            "after_observe": [_name(step) for step in self.after_observe],
        }


def _name(step: Callable[..., object]) -> str:
    """`WorkspaceWatchRule.before_model` 这种, 类名带方法名, 够定位."""
    return str(getattr(step, "__qualname__", repr(step)))


class RuleRunner:
    def __init__(self, table: RuleTable, *, events: LoopEventPublisher) -> None:
        self._table = table
        self._events = events

    @property
    def table(self) -> RuleTable:
        return self._table

    # ---- 五个时机 ----

    def before_model(
        self, view: Callable[[], LoopView], apply: Callable[[Rewrite], None]
    ) -> Continue | LoopStop:
        """`view` 每次现取: 前一条改写了窗口, 后一条要看到. 改写由循环执行 (`apply`)."""
        for step in self._table.before_model:
            verdict = self._guarded(step, view())
            if isinstance(verdict, Rewrite):
                apply(verdict)
            elif isinstance(verdict, LoopStop):
                return verdict
        return Continue()

    def on_model_error(
        self, error: ModelGatewayError, view: LoopView
    ) -> ModelErrorVerdict:
        for step in self._table.on_model_error:
            verdict = self._guarded(step, error, view)
            if not isinstance(verdict, Continue):
                return verdict
        return Continue()

    def after_model(self, outcome: ModelOutcome, view: LoopView) -> AfterModelVerdict:
        for step in self._table.after_model:
            verdict = self._guarded(step, outcome, view)
            if not isinstance(verdict, Continue):
                return verdict
        return Continue()

    def before_dispatch(self, call: ToolCall, view: LoopView) -> BeforeDispatchVerdict:
        for step in self._table.before_dispatch:
            verdict = self._guarded(step, call, view)
            if not isinstance(verdict, Continue):
                return verdict
        return Continue()

    def after_observe(
        self, call: ToolCall, observation: LoopObservation, view: LoopView
    ) -> AfterObserveVerdict:
        for step in self._table.after_observe:
            verdict = self._guarded(step, call, observation, view)
            if not isinstance(verdict, Continue):
                return verdict
        return Continue()

    # ---- 隔离 ----

    def _guarded[V](self, step: Callable[..., V], *args: object) -> V | Continue:
        try:
            return step(*args)
        except Exception as error:
            # 一条规则坏了不该让整轮失败, 但也不能静默: 日志与事件各记一条.
            name = _name(step)
            _log.exception(
                "loop.rule_failed",
                rule=name,
                error=type(error).__name__,
                message=str(error),
            )
            self._events.decision(f"规则 {name} 出错, 已跳过")
            return Continue()
