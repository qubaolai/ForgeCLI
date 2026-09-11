"""人拒绝了, 或者被安全策略拒绝够了次数, 就收掉本轮的工具.

人类拒绝立即收: 他拒绝的是**意图**, 不是那一条命令. 允许换个说法重试, 等于让模型绕过
人刚做的决定 —— 足够执着的模型总能绕过去, 而用户还以为自己有否决权. 这是本轮收, 不是
永久禁: 下一句话该由人说, 他可以纠正也可以换个要求.

策略拒绝数**累计**不数连续: 连续计数会被一次成功的 fs_read 重置, 模型只要在两次被拒
之间插一个无害读取, 计数器就永远回不到上限. 与 repeat_call 不重叠: 那条拦"同一工具同一
参数", 而 `rm -rf build/` 换成 `find build -delete` 是两个签名不同的调用, 它放行. 这里
拦的正是这种换着花样撞墙.
"""

from __future__ import annotations

from forgecli.application.agent_loop.rule import (
    AfterObserveVerdict,
    LoopRuleBase,
    LoopView,
)
from forgecli.application.agent_loop.verdicts import CloseTools, Continue
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.agent.actions import LoopObservation, ObservationDisposition
from forgecli.domain.tool.tool_call import ToolCall
from forgecli.shared.observability.log import get_log

__all__ = ["MAX_BLOCKED_CALLS", "RefusalRule"]

_log = get_log(__name__)

# 本轮累计被安全策略拒绝多少次之后就不再派工具.
MAX_BLOCKED_CALLS = 3


class RefusalRule(LoopRuleBase):
    name = "refusal"

    def __init__(self) -> None:
        self._blocked = 0

    def after_observe(
        self, call: ToolCall, observation: LoopObservation, view: LoopView
    ) -> AfterObserveVerdict:
        if observation.disposition is ObservationDisposition.HALT:
            _log.warning("loop.halt", reason="user_refused")
            return CloseTools(render_notice("loop.halt"))
        if observation.disposition is ObservationDisposition.BLOCKED:
            self._blocked += 1
            _log.warning(
                "loop.blocked_call", blocked=self._blocked, limit=MAX_BLOCKED_CALLS
            )
            if self._blocked >= MAX_BLOCKED_CALLS:
                return CloseTools(render_notice("loop.blocked", count=self._blocked))
        return Continue()
