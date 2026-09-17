"""模型既没有回复也没有请求工具."""

from __future__ import annotations

from forgecli.agent.loop.model_invoker import ModelOutcome
from forgecli.agent.loop.rule import AfterModelVerdict, LoopView
from forgecli.agent.loop.verdicts import Continue
from forgecli.common.observability.log import get_log
from forgecli.core.agent.actions import LoopStop
from forgecli.core.agent.stop import LoopStopReason
from forgecli.prompt import prompts

__all__ = ["EmptyResponseRule"]

_log = get_log(__name__)


class EmptyResponseRule:
    def after_model(self, outcome: ModelOutcome, view: LoopView) -> AfterModelVerdict:
        if not outcome.empty:
            return Continue()
        _log.warning("model.empty_response")
        return LoopStop(
            LoopStopReason.MODEL_ERROR_BLOCKING,
            prompts.STOP_EMPTY_RESPONSE,
        )
