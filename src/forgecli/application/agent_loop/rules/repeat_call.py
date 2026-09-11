"""同一个工具加同一份参数, 一轮里做够了就不再做.

这条是给"模型卡住"准备的, 与总预算是两回事: 总预算拦的是"活干得多", 它拦的是"同一件事
重复做". 见过 fs_find 用相同参数被连着调上百次 —— 每次结果都一样, 模型却读不出该换个
做法, 总预算再大也只是让它多转几百圈.

拒绝之后回填而不是静默跳过: 模型必须知道"你在重复", 否则它只会原样再要一次.
"""

from __future__ import annotations

from forgecli.application.agent_loop.rule import (
    BeforeDispatchVerdict,
    LoopView,
)
from forgecli.application.agent_loop.transcript import render_call
from forgecli.application.agent_loop.verdicts import Continue, Deny
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.tool.tool_call import ToolCall
from forgecli.shared.observability.log import get_log

__all__ = ["MAX_IDENTICAL_CALLS", "RepeatCallRule", "signature_of"]

_log = get_log(__name__)

# 同一个工具 + 同一份参数在一轮里允许重复几次.
#
# 允许 2 次而不是 1 次: 中间穿插过写操作时, 重列一次目录是合理的. 循环判断不了"中间
# 那次调用改没改工作区" —— 那是安全层的知识, 拿进来等于让循环认识工具语义.
#
# 真正的漏网之鱼是**参数每次都变**的原地打转 (8 个 grep 变体各不相同), 它按签名判身份
# 本来就拦不住, 由 barren_streak 那条接手.
MAX_IDENTICAL_CALLS = 2


def signature_of(call: ToolCall) -> str:
    """一次调用的身份: 工具名 + 规范化参数. 参数顺序不同不算不同的调用."""
    return repr(
        (call.name, sorted((str(k), repr(v)) for k, v in call.arguments.items()))
    )


class RepeatCallRule:
    def __init__(self) -> None:
        # (工具名 + 参数) -> 本轮已派发次数.
        self._counts: dict[str, int] = {}

    def before_dispatch(self, call: ToolCall, view: LoopView) -> BeforeDispatchVerdict:
        signature = signature_of(call)
        seen = self._counts.get(signature, 0) + 1
        self._counts[signature] = seen
        if seen <= MAX_IDENTICAL_CALLS:
            return Continue()
        _log.warning(
            "loop.repeat_call_rejected",
            tool=call.name,
            arguments=call.arguments,
            limit=MAX_IDENTICAL_CALLS,
            seen=seen,
        )
        return Deny(
            notice=render_notice(
                "loop.repeat_call", call=render_call(call), limit=MAX_IDENTICAL_CALLS
            ),
            code="repeated_call",
        )
