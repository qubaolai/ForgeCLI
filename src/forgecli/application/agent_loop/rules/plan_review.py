"""工具交出了需要人裁决的东西, 本轮到此为止 (ADR-0023 决策 1).

不收工具: 那条路是"工具没了但你继续说", 而这里模型已经把要说的说完了 —— 它交出了一份
计划, 正等着回话. 再逼它说一段话, 只会在评审界面上方多出一段没人读的文字.

排队中的调用由循环补上配对的工具结果: 本轮的 transcript 到此为止, 但它仍然是这一轮的
完整记录, 而一份缺了配对结果的记录在任何后续消费者眼里都是残缺的.
"""

from __future__ import annotations

from forgecli.application.agent_loop.rule import (
    AfterObserveVerdict,
    LoopView,
)
from forgecli.application.agent_loop.verdicts import Continue
from forgecli.domain.agent.actions import (
    LoopObservation,
    LoopStop,
    ObservationDisposition,
)
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.tool.tool_call import ToolCall

__all__ = ["PlanReviewRule"]


class PlanReviewRule:
    def after_observe(
        self, call: ToolCall, observation: LoopObservation, view: LoopView
    ) -> AfterObserveVerdict:
        if observation.disposition is ObservationDisposition.AWAIT_USER_DECISION:
            return LoopStop(LoopStopReason.WAIT_PLAN_REVIEW)
        return Continue()
