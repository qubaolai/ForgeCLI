"""目录已经收掉了, 模型还在要工具.

靠"没给你看你就不会要"不算强制 —— 真正的强制是这里不派发. 这些 tool_calls 也不写进
transcript: 写进去就欠一份配对的工具结果, 而本轮不会再有执行了.
"""

from __future__ import annotations

from forgecli.application.agent_loop.model_invoker import ModelOutcome
from forgecli.application.agent_loop.rule import (
    AfterModelVerdict,
    LoopView,
)
from forgecli.application.agent_loop.verdicts import Continue, Replace
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.agent.actions import LoopStop
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.shared.observability.log import get_log

__all__ = ["ToolsClosedRule"]

_log = get_log(__name__)


class ToolsClosedRule:
    def after_model(self, outcome: ModelOutcome, view: LoopView) -> AfterModelVerdict:
        if not (view.tools_closed and outcome.tool_calls):
            return Continue()
        has_text = bool(outcome.text.strip())
        _log.warning(
            "loop.tool_calls_after_close",
            requested=[call.name for call in outcome.tool_calls],
            has_text=has_text,
        )
        if not has_text:
            return LoopStop(LoopStopReason.POLICY_DENIED, render_notice("stop.halt"))
        return Replace(ModelOutcome(text=outcome.text))
