"""上下文预算: 一次模型调用的输入能占多少窗口 (ADR-0032 决策 1).

窗口大小是 ``ModelCatalogEntry`` 的事实, 但**循环不认识模型目录** —— ``SIBLING_BANS``
禁了 ``application.agent_loop -> application.llm.gateway.default_gateway``, 让循环自己
去查等于绕开那条禁令. 所以它被折成这个纯值对象, 由 AgentTurnService 在编排时取好,
经 ``ContextPackage`` 塞进 ``LoopInput``.

阈值不设成 100%: 压缩本身要留余量 (摘要那一次调用也要占窗口), 而且
``ApproximateTokenEstimator`` 按 4 字符折 1 token, 与供应商真实分词必然有偏差. 贴着
硬上限走, 等于把这份偏差留给运行期去撞 —— 撞上就是一整轮白跑.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ContextBudget"]


@dataclass(frozen=True)
class ContextBudget:
    """一次模型调用的输入预算."""

    context_window: int
    # 给这次回复留出的输出空间. 取模型的 max_output_tokens; 未知时为 0.
    reserved_output_tokens: int = 0
    # 输入占到可用额度的多少就开始压.
    compact_ratio: float = 0.75

    def __post_init__(self) -> None:
        if self.context_window <= 0:
            raise ValueError("ContextBudget.context_window 必须为正整数")
        if self.reserved_output_tokens < 0:
            raise ValueError("ContextBudget.reserved_output_tokens 不能为负")
        if not 0.0 < self.compact_ratio <= 1.0:
            raise ValueError("ContextBudget.compact_ratio 必须落在 (0, 1]")

    @property
    def allowance(self) -> int:
        """输入的硬上限: 窗口减去留给输出的部分.

        下限取 1 而不是让它变成 0 或负数: 一个把整个窗口都预留给输出的配置是配错了,
        但压缩器不该因此除零或者进入"怎么压都不够"的死循环. 这里给出一个必然超限的
        额度, 让上层按"压不下去"正常收尾.
        """
        return max(1, self.context_window - self.reserved_output_tokens)

    @property
    def trigger(self) -> int:
        """开始压缩的阈值."""
        return max(1, int(self.allowance * self.compact_ratio))

    def needs_compaction(self, estimated_input: int) -> bool:
        return estimated_input > self.trigger

    def over_allowance(self, estimated_input: int) -> bool:
        """压完之后还超不超. 超了就只能按 CONTEXT_COMPACTION_REQUIRED 停下."""
        return estimated_input > self.allowance
