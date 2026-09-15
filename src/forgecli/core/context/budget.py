"""上下文预算: 一次请求能占多少, 窗口的两条水位在哪 (ADR-0041 决策 5).

窗口大小是 ``ModelCatalogEntry`` 的事实, 但**循环不认识模型目录** —— ``SIBLING_BANS``
禁了 ``application.agent_loop -> application.llm.gateway.default_gateway``, 让循环自己
去查等于绕开那条禁令. 所以它被折成这个纯值对象, 由 AgentTurnService 在编排时取好.

## 两个上限, 不是一个

    硬限  context_window - reserved_output_tokens   超了供应商报错
    软限  effective_context_tokens                  超了不报错, 只是模型开始漏读

注意力衰减是**绝对 token 数**的函数, 不是填充比例: 同样 30k 内容放在 200k 窗口 (15%)
与放在 32k 窗口 (94%) 里, 模型对那 30k 的处理能力基本一样, 后者的区别只是快撞硬限了.
所以按标称窗口的百分比定水位是错的 —— 一个 200k 的模型会得出 150k 的高水位, 而那远超
任何模型能可靠工作的长度.

软限未知时取 ``context_window``, 整条退化为无操作, 由硬限那一支兜底. 不猜一个数: 猜小了
平白丢内容, 猜大了等于没有这道防线.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ContextBudget"]

# 低水位取高水位的几成. 这个比值决定**淘汰频率**: 贴太近就变成每几步淘汰一次 (前缀反复
# 作废, 退化成 ADR-0041 决策 5 要避免的滑动窗口); 压太低就一次丢掉过多.
_LOW_WATER_RATIO = 0.4


@dataclass(frozen=True)
class ContextBudget:
    """一次模型调用的预算与窗口水位."""

    context_window: int
    # 给这次回复留出的输出空间. 取模型的 max_output_tokens; 未知时为 0.
    reserved_output_tokens: int = 0
    # 这个模型能可靠工作的长度. None 表示未知, 取 context_window.
    effective_context_tokens: int | None = None
    # 自动压缩的触发阈值, 兼防估算偏差.
    #
    # 0.80 而不是更保守的数, 理由有二: 软限现在承担了"别让上下文长到伤注意力"这件事;
    # 而摘要那次调用的输入是被淘汰区间, 是当前窗口的**子集**, 它必然放得下 —— 原来为它
    # 留的那份余量是多算的.
    #
    # 估算偏差方向是安全的: ApproximateTokenEstimator 对 CJK 按一字符一 token, 拉丁与
    # 代码按 3 字符一 token (实测 deepseek 3.05 / GLM 3.89, 取 3 不会少算), 且已把
    # tool_calls 的参数算进去. 它系统性**高估**.
    compact_ratio: float = 0.80

    def __post_init__(self) -> None:
        if self.context_window <= 0:
            raise ValueError("ContextBudget.context_window 必须为正整数")
        if self.reserved_output_tokens < 0:
            raise ValueError("ContextBudget.reserved_output_tokens 不能为负")
        if not 0.0 < self.compact_ratio <= 1.0:
            raise ValueError("ContextBudget.compact_ratio 必须落在 (0, 1]")
        if self.effective_context_tokens is not None and (
            self.effective_context_tokens <= 0
        ):
            raise ValueError("ContextBudget.effective_context_tokens 必须为正整数")

    @property
    def allowance(self) -> int:
        """硬限: 窗口减去留给输出的部分.

        下限取 1 而不是让它变成 0 或负数: 一个把整个窗口都预留给输出的配置是配错了,
        但不该因此除零. 这里给出一个必然超限的额度, 让上层按"放不下"正常收尾.
        """
        return max(1, self.context_window - self.reserved_output_tokens)

    @property
    def request_high_water(self) -> int:
        """撞到它就淘汰. 软限与硬限取更小的那个."""
        soft = self.effective_context_tokens or self.context_window
        hard = int(self.allowance * self.compact_ratio)
        return max(1, min(soft, hard))

    @property
    def request_low_water(self) -> int:
        """淘汰淘到它为止."""
        return max(1, int(self.request_high_water * _LOW_WATER_RATIO))

    def window_allowance(self, prefix_tokens: int) -> int:
        """扣掉前缀之后, 留给会话窗口的那部分.

        水位管的是**整条请求** —— 注意力与硬限都是对整条请求而言的, 而工具目录, 静态
        策略与运行上下文同样占位置. 只按窗口自己算水位, 等于把前缀那几千 token 漏掉.
        """
        return max(0, self.request_high_water - prefix_tokens)

    def window_floor(self, prefix_tokens: int) -> int:
        """淘汰的目标: 扣掉前缀之后的低水位."""
        return max(0, self.request_low_water - prefix_tokens)
