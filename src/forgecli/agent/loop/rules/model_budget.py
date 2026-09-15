"""模型调用次数上限."""

from __future__ import annotations

from forgecli.agent.loop.rule import BeforeModelVerdict, LoopView
from forgecli.agent.loop.verdicts import Continue
from forgecli.common.observability.log import get_log
from forgecli.core.agent.actions import LoopStop
from forgecli.core.agent.stop import LoopStopReason
from forgecli.prompt.template_renderer import render_notice

__all__ = ["DEFAULT_MAX_MODEL_CALLS", "ModelBudgetRule"]

_log = get_log(__name__)

# 单轮内模型调用的兜底上限, 防止模型与工具互相喂招停不下来. 工具调用本身不设总数上限.
DEFAULT_MAX_MODEL_CALLS = 9999


class ModelBudgetRule:
    def __init__(self, *, limit: int = DEFAULT_MAX_MODEL_CALLS) -> None:
        self._limit = limit

    @property
    def limit(self) -> int:
        return self._limit

    def before_model(self, view: LoopView) -> BeforeModelVerdict:
        if view.model_calls < self._limit:
            return Continue()
        _log.warning("loop.model_budget_exhausted", limit=self._limit)
        return LoopStop(
            LoopStopReason.BUDGET_EXHAUSTED,
            render_notice("stop.model_budget", limit=self._limit),
        )
