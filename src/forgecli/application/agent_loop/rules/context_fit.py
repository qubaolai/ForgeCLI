"""窗口放不放得下: 调模型之前先压, 供应商说溢出之后强压 (ADR-0041 决策 5).

两道防线是同一个问题, 所以在同一个文件里: 估算与真实分词永远有偏差, 第二道是第一道
的下界, 不是它的备份.
"""

from __future__ import annotations

from forgecli.application.agent_loop.rule import (
    BeforeModelVerdict,
    LoopRuleBase,
    LoopView,
    ModelErrorVerdict,
)
from forgecli.application.agent_loop.verdicts import Continue, Rewrite
from forgecli.application.context.window_manager import WindowManager
from forgecli.application.llm.gateway.errors import (
    ModelContextOverflowError,
    ModelGatewayError,
)
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.agent.actions import LoopStop
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.shared.observability.log import get_log

__all__ = ["ContextFitRule"]

_log = get_log(__name__)


class ContextFitRule(LoopRuleBase):
    name = "context_fit"

    def __init__(self, context: WindowManager | None) -> None:
        # 缺省为 None: 没给窗口管理就不压缩, 不猜一个窗口大小.
        self._context = context
        # 供应商说上下文太长之后强压过几次. 只给一次: 淘汰跑完之后窗口只剩一段交接
        # 说明加最近几条, 再超就不是淘汰能解决的问题了.
        self._overflow_compactions = 0

    def before_model(self, view: LoopView) -> BeforeModelVerdict:
        """看一眼窗口撞没撞高水位.

        放在这里而不是等网关抛 ModelContextOverflowError: 那条路上请求已经组好了, 一轮
        跑了二十步的工作会因为最后一次调用超窗而整个作废.

        淘汰完仍然放不下时停在 CONTEXT_COMPACTION_REQUIRED, 不把一个必然被拒的请求发
        出去. 它是可恢复的暂停: 这一轮干不下去了, 但会话没坏.
        """
        if self._context is None:
            return Continue()
        result = self._context.fit(
            view.window,
            budget=view.budget,
            session_id=view.session_id,
            turn_id=view.turn_id,
            system_prompt=view.system_prompt,
            tools=view.tools,
        )
        view.ledger.record_fit(result)
        if result.drafts:
            _log.info(
                "window.fit",
                drafts=len(result.drafts),
                tokens_saved=sum(draft.tokens_saved for draft in result.drafts),
                messages=len(result.window.messages),
                over_allowance=result.over_allowance,
            )
        if result.over_allowance:
            _log.error("window.over_allowance", messages=len(result.window.messages))
            return LoopStop(
                LoopStopReason.CONTEXT_COMPACTION_REQUIRED,
                render_notice("context.compaction_failed"),
            )
        if result.drafts:
            return Rewrite(result.window)
        return Continue()

    def on_model_error(
        self, error: ModelGatewayError, view: LoopView
    ) -> ModelErrorVerdict:
        """供应商说上下文太长: 强压一次再发, 而不是让这一轮作废.

        走到这里说明估算与供应商的分词对不上. 降级路径是强制一次淘汰 (`force=True`
        跳过水位判断). 不另开一个入口: 两条路各自决定切点与保留策略, 迟早有一条漏掉
        "用户原话逐字保留"这类规则.
        """
        if not isinstance(error, ModelContextOverflowError):
            return Continue()
        if self._context is None or view.budget is None or self._overflow_compactions:
            _log.error("window.overflow_unrecoverable", message=str(error))
            return self._compaction_required()
        self._overflow_compactions += 1
        result = self._context.fit(
            view.window,
            budget=view.budget,
            force=True,
            session_id=view.session_id,
            turn_id=view.turn_id,
            system_prompt=view.system_prompt,
            tools=view.tools,
        )
        view.ledger.record_fit(result)
        if not result.drafts:
            _log.error("window.overflow_uncompactable", message=str(error))
            return self._compaction_required()
        _log.warning(
            "window.overflow_evicted",
            message=str(error),
            messages=len(result.window.messages),
            estimated_input=result.estimated_input,
        )
        return Rewrite(result.window)

    @staticmethod
    def _compaction_required() -> LoopStop:
        return LoopStop(
            LoopStopReason.CONTEXT_COMPACTION_REQUIRED,
            render_notice("context.compaction_failed"),
        )
