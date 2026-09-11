"""内置规则表 (ADR-0049 决策 3).

这份列表是这次拆分最要紧的一个文件, 不是配置: 顺序就是书写顺序, 每一项带一行为什么
排在这里. 硬约束另外写在各条规则的 `runs_before` / `runs_after` 上, 装配时校验.
"""

from __future__ import annotations

from forgecli.application.agent_loop.rule import LoopRule
from forgecli.application.agent_loop.rules.context_fit import ContextFitRule
from forgecli.application.agent_loop.rules.empty_response import EmptyResponseRule
from forgecli.application.agent_loop.rules.malformed_output import (
    MalformedOutputRule,
)
from forgecli.application.agent_loop.rules.model_budget import ModelBudgetRule
from forgecli.application.agent_loop.rules.tools_closed import ToolsClosedRule
from forgecli.application.context.window_manager import WindowManager

__all__ = ["builtin_rules"]


def builtin_rules(*, context: WindowManager | None) -> tuple[LoopRule, ...]:
    """每轮现建: 好几条规则带着本轮的计数器."""
    return (
        # 预算先于压缩: 该停了就别再为压缩花一次模型调用 (它自己声明了这条约束).
        ModelBudgetRule(),
        # 压缩紧跟预算: 调模型之前最后一道, 看到的是这一轮要发出去的窗口.
        ContextFitRule(context),
        # 模型回来之后, 先看它有没有说话.
        EmptyResponseRule(),
        # 再看它说的话能不能用. 排在目录已收之前, 理由写在它的约束里.
        MalformedOutputRule(),
        # 最后才问"目录已收你还要工具": 前面两条都过了, 这才是模型自己的选择.
        ToolsClosedRule(),
    )
