"""内置规则表.

这份表就是整个循环的决策: 五个时机, 每个时机跑哪些方法, 什么顺序, 为什么这么排.
读完这一个文件就够; 想看某一条怎么判, 再去它自己的文件.

同一个对象的方法出现在几列, 它就参与几个时机, 状态自然共享. 顺序的理由写在注释

每轮现建: 好几条规则带着本轮的计数器.
"""

from __future__ import annotations

from forgecli.agent.loop.rule_table import RuleTable
from forgecli.agent.loop.rules.barren_streak import BarrenStreakRule
from forgecli.agent.loop.rules.context_fit import ContextFitRule
from forgecli.agent.loop.rules.empty_response import EmptyResponseRule
from forgecli.agent.loop.rules.malformed_output import MalformedOutputRule
from forgecli.agent.loop.rules.model_budget import (
    DEFAULT_MAX_MODEL_CALLS,
    ModelBudgetRule,
)
from forgecli.agent.loop.rules.plan_review import PlanReviewRule
from forgecli.agent.loop.rules.refusal import RefusalRule
from forgecli.agent.loop.rules.repeat_call import RepeatCallRule
from forgecli.agent.loop.rules.tools_closed import ToolsClosedRule
from forgecli.conversation.window_manager import WindowManager

__all__ = ["builtin_rules"]


def builtin_rules(
    *, context: WindowManager | None, max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
) -> RuleTable:
    # 挂了不止一个时机的几条先建好, 下面各列引用同一个对象.
    fit = ContextFitRule(context)
    malformed = MalformedOutputRule()

    # 读过的文件被外部改了的通知不在这里: 它是 assemble() 带回来的通知之一
    # (conversation/notices.py 决策 14), 循环在 before_model 之前就已经追加进窗口,
    # 所以这次压缩自然把它算进预算.
    return RuleTable(
        before_model=(
            # 该停了就别再为压缩花一次模型调用: 那次调用记进用量却什么也没换来.
            ModelBudgetRule(limit=max_model_calls).before_model,
            # 调模型之前最后一道, 看到的是这一轮要发出去的窗口.
            fit.before_model,
        ),
        on_model_error=(
            # 两条各认各的错, 互不重叠; 都不认的由循环停轮.
            fit.on_overflow,
            malformed.on_bad_json,
        ),
        after_model=(
            # 先看它有没有说话.
            EmptyResponseRule().after_model,
            # 再看它说的话能不能用. 一个格式坏掉的调用不该被当成"无视目录已收"的
            # 证据, 所以排在下一条之前.
            malformed.after_model,
            # 最后才问"目录已收你还要工具": 前面都过了, 这才是模型自己的选择.
            ToolsClosedRule().after_model,
        ),
        before_dispatch=(RepeatCallRule().before_dispatch,),
        after_observe=(
            # 先判"轮到人说话", 再判"你被罚闭嘴": 两者都不再派工具, 判反了评审界面
            # 上方会多一段没人读的解释.
            PlanReviewRule().after_observe,
            RefusalRule().after_observe,
            # 空转提醒最后: 前两条任一命中, 这一条就不用再数了.
            BarrenStreakRule().after_observe,
        ),
    )
