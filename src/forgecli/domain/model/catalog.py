"""模型目录条目: 一个已配置模型的能力与价格（ADR-0011 §4）。

上下文窗口、能力标志、thinking 支持、四项价格, 都是「这个模型能做什么」的事实,
与目录从 TOML 读还是从远端拉无关, 故属领域。ModelCatalogService 那个读端口留在
application。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forgecli.domain.model.thinking import (
    ModelThinkingCapabilities,
    ModelThinkingSettings,
    ThinkingEffortName,
    ThinkingMode,
)


def _default_thinking_options() -> ModelThinkingCapabilities:
    return ModelThinkingCapabilities()


def _disabled_thinking() -> ModelThinkingSettings:
    return ModelThinkingSettings(mode=ThinkingMode.OFF)


@dataclass(frozen=True)
class ModelCatalogEntry:
    """单模型的只读元数据边界（ADR-0011 §4）。

    provider/model 唯一定位一个模型；能力位与窗口供 ModelSelectionResolver 做能力前置
    校验。价格元数据（§4 / §11.2）供 CostEstimator 只读；None 表示价格未知，
    不阻塞调用（estimated_cost=None）。
    """

    provider: str
    model: str
    context_window: int
    max_output_tokens: int | None = None
    # 这个模型能**可靠工作**的长度, 与标称窗口是两回事 (ADR-0041 决策 5).
    #
    # 注意力衰减是绝对 token 数的函数, 不是填充比例: 标称 200k 的模型在装到 150k 时早已
    # 开始漏读, 而它不会报错. 这个数推不出来, 只能按模型填, 所以它与 context_window 并列
    # 住在这里 —— 别处没有地方放.
    #
    # None 表示未知, 上下文预算据此退化为只按硬限兜底; 不猜一个数.
    effective_context_tokens: int | None = None
    supports_structured_output: bool = False
    supports_tool_calling: bool = False
    thinking_capabilities: ModelThinkingCapabilities = field(
        default_factory=_default_thinking_options
    )
    thinking_settings: ModelThinkingSettings = field(default_factory=_disabled_thinking)
    allowlisted: bool = True
    deprecated: bool = False
    # 价格（每 1k token；None = 未知）。cached 价格缺失时按 input 价计；
    # reasoning 价格缺失时 reasoning token 并入 output 计价（ADR-0012 §7）。
    input_price_per_1k: float | None = None
    output_price_per_1k: float | None = None
    cached_input_price_per_1k: float | None = None
    reasoning_price_per_1k: float | None = None

    @property
    def thinking_mode(self) -> ThinkingMode:
        return self.thinking_settings.mode

    @property
    def thinking_effort(self) -> ThinkingEffortName | None:
        """用户显式选择的强度；None 表示使用模型默认值。"""
        return self.thinking_settings.effort

    @property
    def effective_thinking_effort(self) -> ThinkingEffortName | None:
        """发送请求时实际使用的强度。"""
        return self.thinking_capabilities.effective_effort(self.thinking_settings)

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("ModelCatalogEntry 的 provider 与 model 都不能为空")
        if self.context_window <= 0:
            raise ValueError("ModelCatalogEntry.context_window 必须为正整数")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("ModelCatalogEntry.max_output_tokens 必须为正整数")
        for name in (
            "input_price_per_1k",
            "output_price_per_1k",
            "cached_input_price_per_1k",
            "reasoning_price_per_1k",
        ):
            price = getattr(self, name)
            if price is not None and price < 0:
                raise ValueError(f"ModelCatalogEntry.{name} 不能为负")
        try:
            self.thinking_capabilities.validate(self.thinking_settings)
        except ValueError as exc:
            raise ValueError(f"ModelCatalogEntry thinking 配置无效: {exc}") from exc
