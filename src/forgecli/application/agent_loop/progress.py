"""判断这一轮有没有在原地打转, 以及该不该收掉工具 (ADR-0010 §13-3).

四道闸, 各拦一类卡住的方式, 彼此不重叠 —— 每条的阈值为什么是这个数, 见常量上面的
说明。它们共同的形状是: 攒计数, 到线了给一个处置, 不自己改控制流。

从 ``builtin_loop`` 分出来是因为这是一套**策略**: 读它要连着四条阈值的理由一起读, 而
读控制流的人不需要知道其中任何一条。循环拿到的只是"要不要收工具 / 要不要提醒一句"。
"""

from __future__ import annotations

from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.agent.actions import LoopObservation, ObservationDisposition
from forgecli.domain.tool.tool_call import ToolCall
from forgecli.shared.observability.log import get_log

__all__ = ["ProgressGuard", "signature_of"]

_log = get_log(__name__)

# 同一个工具 + 同一份参数在一轮里允许重复几次.
#
# 这道闸是给"模型卡住"准备的, 与总预算是两回事: 总预算拦的是"活干得多", 它拦的是
# "同一件事重复做". 见过 fs_find 用相同参数被连着调上百次 —— 每次结果都一样,
# 模型却读不出该换个做法, 总预算再大也只是让它多转几百圈.
#
# 允许 2 次而不是 1 次: 中间穿插过写操作时, 重列一次目录是合理的.
#
# 2026-08-19 重新评估过降到 1, 结论是不降. 上面那条"写完再列一次"是真实场景, 而循环判断
# 不了"中间那次调用改没改工作区" —— 那是安全层的知识, 拿进来等于让循环认识工具语义.
# 真正的漏网之鱼是**参数每次都变**的原地打转 (8 个 grep 变体各不相同), 它按签名判身份
# 本来就拦不住, 该由 _MAX_BARREN_OBSERVATIONS 接手.
MAX_IDENTICAL_CALLS = 2

# 本轮累计被安全策略拒绝多少次之后就不再派工具.
#
# 数**累计**不数连续: 连续计数会被一次成功的 fs_read_file 重置, 模型只要在两次被拒之间
# 插一个无害读取, 计数器就永远回不到上限.
#
# 与 MAX_IDENTICAL_CALLS 不重叠: 那道闸拦"同一工具同一参数", 而 `rm -rf build/` 换成
# `find build -delete` 是两个签名不同的调用, 它放行. 这里拦的正是这种换着花样撞墙.
MAX_BLOCKED_CALLS = 3

# 连续多少次工具调用没带回新信息之后提醒一次.
#
# 这道闸补的是 MAX_IDENTICAL_CALLS 的盲区: 那道闸按**入参**判身份, 而一次真实任务里
# 8 个 grep 变体的参数各不相同 (加个 --include, 加个 | head, 换个转义), 全部放行, 返回
# 的却都是同一个空结果. 真正的浪费信号不是"参数一样", 是"结果没告诉我新东西".
#
# 只提醒, 不收工具: 空结果不是错误, 模型该做的是换个思路或者直接报告"没找到", 而这两件
# 事都还需要工具. 收掉目录等于因为没找到就罚它闭嘴.
MAX_BARREN_OBSERVATIONS = 3


def signature_of(call: ToolCall) -> str:
    """一次调用的身份: 工具名 + 规范化参数. 参数顺序不同不算不同的调用."""
    return repr(
        (call.name, sorted((str(k), repr(v)) for k, v in call.arguments.items()))
    )


class ProgressGuard:
    """一轮之内的卡死判据。每轮一个新实例, 计数不跨轮。"""

    def __init__(self) -> None:
        # (工具名 + 参数) -> 本轮已派发次数, 用于挡住原地打转.
        self._call_counts: dict[str, int] = {}
        self._blocked_calls = 0
        # 连续几次工具调用没带回新信息, 以及上一次带回的是什么.
        self._barren_streak = 0
        self._last_observation: tuple[str, str] | None = None

    def is_repeat(self, call: ToolCall) -> bool:
        """记一次派发, 并回答"这个调用已经做够了吗"。"""
        signature = signature_of(call)
        seen = self._call_counts.get(signature, 0) + 1
        self._call_counts[signature] = seen
        if seen <= MAX_IDENTICAL_CALLS:
            return False
        _log.warning(
            "loop.repeat_call_rejected",
            tool=call.name,
            arguments=call.arguments,
            limit=MAX_IDENTICAL_CALLS,
            seen=seen,
        )
        return True

    def repeat_notice(self, rendered_call: str) -> str:
        return render_notice(
            "loop.repeat_call", call=rendered_call, limit=MAX_IDENTICAL_CALLS
        )

    def track(self, observation: LoopObservation) -> None:
        """记一次调用有没有带回新信息.

        判据两条, 满足其一就算没有: 内容为空, 或与上一次的**摘要加句柄**完全相同.

        比的是 `(content, handle)` 而不是只比 content: ADR-0041 把正文移出窗口之后
        content 短了很多, 两次不同的调用更容易撞出同一段文字 —— 而句柄是内容寻址的,
        正文不一样句柄就不一样. 没有句柄的结果 (计划, 记忆一类) 退回只比 content,
        它们本来也不归档.

        失败的观察不参与计数 —— 它们有专门的闸 (MAX_BLOCKED_CALLS 与 HALT). 两个计数器
        数同一件事, 事后就说不清到底是哪条规则停的.
        """
        if observation.is_error:
            return
        content = observation.content.strip()
        fingerprint = (content, observation.handle)
        if not content or fingerprint == self._last_observation:
            self._barren_streak += 1
        else:
            self._barren_streak = 0
        self._last_observation = fingerprint

    def barren_nudge(self) -> tuple[str, str] | None:
        """到了连续次数就给 (提醒正文, 决策摘要), 并把计数清零。

        清零是因为不清的话之后每一步都会再提醒一次。
        """
        if self._barren_streak < MAX_BARREN_OBSERVATIONS:
            return None
        count = self._barren_streak
        self._barren_streak = 0
        _log.warning("loop.barren_streak", count=count)
        return (
            render_notice("loop.barren", count=count),
            f"连续 {count} 次调用没有新信息",
        )

    def should_close_tools(self, observation: LoopObservation) -> str | None:
        """按观察的处置意见决定要不要收掉本轮的工具. 返回收摊理由, None 表示继续.

        人类拒绝立即收: 他拒绝的是**意图**, 不是那一条命令. 允许换个说法重试, 等于让
        模型绕过人刚做的决定 —— 足够执着的模型总能绕过去, 而用户还以为自己有否决权.
        这是本轮收, 不是永久禁: 下一句话该由人说, 他可以纠正也可以换个要求.
        """
        if observation.disposition is ObservationDisposition.HALT:
            _log.warning("loop.halt", reason="user_refused")
            return render_notice("loop.halt")
        if observation.disposition is ObservationDisposition.BLOCKED:
            self._blocked_calls += 1
            _log.warning(
                "loop.blocked_call",
                blocked=self._blocked_calls,
                limit=MAX_BLOCKED_CALLS,
            )
            if self._blocked_calls >= MAX_BLOCKED_CALLS:
                return render_notice("loop.blocked", count=self._blocked_calls)
        return None
