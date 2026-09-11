"""模型既没有回复也没有请求工具."""

from __future__ import annotations

from forgecli.application.agent_loop.model_invoker import ModelOutcome
from forgecli.application.agent_loop.rule import (
    AfterModelVerdict,
    LoopView,
)
from forgecli.application.agent_loop.verdicts import Continue
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.agent.actions import LoopStop
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.shared.observability.log import get_log

__all__ = ["EmptyResponseRule"]

_log = get_log(__name__)


class EmptyResponseRule:
    def after_model(self, outcome: ModelOutcome, view: LoopView) -> AfterModelVerdict:
        if not outcome.empty:
            return Continue()
        _log.warning("model.empty_response")
        return LoopStop(
            LoopStopReason.MODEL_ERROR_BLOCKING,
            render_notice("stop.empty_response"),
        )
