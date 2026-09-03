"""提示词编译期的失败 (ADR-0042 决策 4).

只有一种: 静态提示词超出预算. 它是**构建期**失败而不是运行期降级 —— 提示词是固定预算下
的取舍, 加一条规则就得先删一条. 让超额的改动在构建时直接失败, 是这条棘轮唯一能生效的
方式; 换成警告或截断, 预算就只是一句建议.
"""

from __future__ import annotations

__all__ = ["PromptBudgetExceeded"]


class PromptBudgetExceeded(RuntimeError):
    """静态提示词超出 PROMPT_STATIC_BUDGET."""

    def __init__(self, *, estimated: int, budget: int) -> None:
        super().__init__(
            f"静态提示词约 {estimated} token, 超出预算 {budget}. "
            "预算只能下调: 要加一条规则, 先删一条 (ADR-0042 决策 4)."
        )
        self.estimated = estimated
        self.budget = budget
