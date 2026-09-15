"""连续几次工具调用没带回新信息, 提醒一句.

这条补的是 repeat_call 的盲区: 那条按**入参**判身份, 而一次真实任务里 8 个 grep 变体的
参数各不相同 (加个 --include, 加个 | head, 换个转义), 全部放行, 返回的却都是同一个空
结果. 真正的浪费信号不是"参数一样", 是"结果没告诉我新东西".

只提醒, 不收工具: 空结果不是错误, 模型该做的是换个思路或者直接报告"没找到", 而这两件
事都还需要工具.
"""

from __future__ import annotations

from forgecli.agent.loop.rule import AfterObserveVerdict, LoopView
from forgecli.agent.loop.verdicts import Continue, Inject
from forgecli.common.observability.log import get_log
from forgecli.core.agent.actions import LoopObservation
from forgecli.core.message import ChatMessage, TextBlock
from forgecli.core.tool.tool_call import ToolCall
from forgecli.core.turn import MessageRole
from forgecli.prompt.template_renderer import render_notice

__all__ = ["MAX_BARREN_OBSERVATIONS", "BarrenStreakRule"]

_log = get_log(__name__)

# 连续多少次工具调用没带回新信息之后提醒一次.
MAX_BARREN_OBSERVATIONS = 3


class BarrenStreakRule:
    def __init__(self) -> None:
        self._streak = 0
        self._last: tuple[str, str] | None = None

    def after_observe(
        self, call: ToolCall, observation: LoopObservation, view: LoopView
    ) -> AfterObserveVerdict:
        """判据两条, 满足其一就算没有新信息: 内容为空, 或与上一次的摘要加句柄完全相同.

        比的是 (content, handle) 而不是只比 content: ADR-0041 把正文移出窗口之后
        content 短了很多, 两次不同的调用更容易撞出同一段文字 —— 而句柄是内容寻址的,
        正文不一样句柄就不一样.

        失败的观察不参与计数: 它们有专门的规则 (refusal). 两个计数器数同一件事, 事后
        就说不清到底是哪条规则停的.
        """
        if observation.is_error:
            return Continue()
        content = observation.content.strip()
        fingerprint = (content, observation.handle)
        if not content or fingerprint == self._last:
            self._streak += 1
        else:
            self._streak = 0
        self._last = fingerprint
        if self._streak < MAX_BARREN_OBSERVATIONS:
            return Continue()
        # 提醒过就清零, 不然之后每一步都会再提醒一次.
        count = self._streak
        self._streak = 0
        _log.warning("loop.barren_streak", count=count)
        notice = render_notice("loop.barren", count=count)
        return Inject(
            messages=(
                ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),)),
            ),
            summary=f"连续 {count} 次调用没有新信息",
        )
