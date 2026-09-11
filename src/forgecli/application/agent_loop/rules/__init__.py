"""内置规则表 (ADR-0049 决策 3, 2026-09-11 修订).

这份表就是整个循环的决策: 五个时机, 每个时机跑哪些方法, 什么顺序, 为什么这么排.
读完这一个文件就够; 想看某一条怎么判, 再去它自己的文件.

同一个对象的方法出现在几列, 它就参与几个时机, 状态自然共享. 顺序的理由写在旁边,
由 tests/agent_loop/test_rule_table.py 守着: 改错顺序 `make test` 停.

每轮现建: 好几条规则带着本轮的计数器.
"""

from __future__ import annotations

from forgecli.application.agent_loop.rule_table import RuleTable
from forgecli.application.agent_loop.rules.barren_streak import BarrenStreakRule
from forgecli.application.agent_loop.rules.context_fit import ContextFitRule
from forgecli.application.agent_loop.rules.empty_response import EmptyResponseRule
from forgecli.application.agent_loop.rules.malformed_output import (
    MalformedOutputRule,
)
from forgecli.application.agent_loop.rules.model_budget import ModelBudgetRule
from forgecli.application.agent_loop.rules.plan_review import PlanReviewRule
from forgecli.application.agent_loop.rules.refusal import RefusalRule
from forgecli.application.agent_loop.rules.repeat_call import RepeatCallRule
from forgecli.application.agent_loop.rules.tools_closed import ToolsClosedRule
from forgecli.application.agent_loop.rules.workspace_watch import WorkspaceWatchRule
from forgecli.application.context.window_manager import WindowManager
from forgecli.application.workspace.monitor import WorkspaceSnapshotProvider

__all__ = ["builtin_rules"]


def builtin_rules(
    *,
    context: WindowManager | None,
    workspace_provider: WorkspaceSnapshotProvider | None,
) -> RuleTable:
    # 挂了不止一个时机的几条先建好, 下面各列引用同一个对象.
    workspace = WorkspaceWatchRule(workspace_provider)
    fit = ContextFitRule(context)
    malformed = MalformedOutputRule()

    return RuleTable(
        before_model=(
            # 工作区通知要先进窗口, 这次压缩才把它算进预算. 反过来一次大改动的变更
            # 列表 (几 KB) 不进本次估算, 而它恰恰最可能把窗口顶过水位.
            workspace.before_model,
            # 该停了就别再为压缩花一次模型调用: 那次调用记进用量却什么也没换来.
            ModelBudgetRule().before_model,
            # 调模型之前最后一道, 看到的是这一轮要发出去的窗口.
            fit.before_model,
        ),
        on_model_error=(
            # 两条各认各的错, 互不重叠; 都不认的由循环停轮.
            fit.on_overflow,
            malformed.on_bad_json,
        ),
        after_model=(
            # 生成期间工作区变了且本来要直接回答: 先重问, 别的都不用看.
            workspace.after_model,
            # 先看它有没有说话.
            EmptyResponseRule().after_model,
            # 再看它说的话能不能用. 一个格式坏掉的调用不该被当成"无视目录已收"的
            # 证据, 所以排在下一条之前.
            malformed.after_model,
            # 最后才问"目录已收你还要工具": 前面都过了, 这才是模型自己的选择.
            ToolsClosedRule().after_model,
        ),
        before_dispatch=(
            # 派工具之前先拍一眼外部变化, 只攒不说.
            workspace.before_dispatch,
            RepeatCallRule().before_dispatch,
        ),
        after_observe=(
            # 刚跑完的工具改了什么, 归它.
            workspace.after_observe,
            # 先判"轮到人说话", 再判"你被罚闭嘴": 两者都不再派工具, 判反了评审界面
            # 上方会多一段没人读的解释.
            PlanReviewRule().after_observe,
            RefusalRule().after_observe,
            # 空转提醒最后: 前两条任一命中, 这一条就不用再数了.
            BarrenStreakRule().after_observe,
        ),
    )
