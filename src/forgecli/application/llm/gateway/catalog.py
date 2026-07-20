"""模型目录只读视图 ModelCatalogService（ADR-0011 §4）。

ModelCatalogService 是*单模型能力*的唯一只读事实来源：provider/model 存在性、
context window、structured output、tool calling、thinking、allowlist 等元数据都从
这里读，gateway 与 ModelSelectionResolver 都不各自解析 TOML。

与 ProviderCapabilities 的分工（§3.2 / §4）：ProviderCapabilities 只描述 adapter /
provider 级能力（协议特性、streaming / tool schema 变体）；单模型能力一律以本 service
为准，路由与能力过滤都读 catalog，不复述 provider 能力。

今日只冻结接口与元数据边界（0701）：不实现任何具体视图。真实视图（内置 baseline 与用户
`[providers.*.models.*]` TOML 合并成的只读运行时视图）留给后续切片，本切片不解析配置。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from forgecli.application.llm.gateway.params import ThinkingEffort, ThinkingMode
from forgecli.application.llm.model_ref import ModelRef


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
    supports_structured_output: bool = False
    supports_tool_calling: bool = False
    supports_thinking: bool = False
    # 具体模型的 thinking 默认值；请求层不得覆盖。
    thinking_mode: ThinkingMode = ThinkingMode.AUTO
    thinking_effort: ThinkingEffort = ThinkingEffort.NONE
    allowlisted: bool = True
    deprecated: bool = False
    # 价格（每 1k token；None = 未知）。cached 价格缺失时按 input 价计；
    # reasoning 价格缺失时 reasoning token 并入 output 计价（ADR-0012 §7）。
    input_price_per_1k: float | None = None
    output_price_per_1k: float | None = None
    cached_input_price_per_1k: float | None = None
    reasoning_price_per_1k: float | None = None

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


class ModelCatalogService(ABC):
    """单模型能力的唯一只读事实来源。今日仅冻结接口，不实现具体视图。"""

    @abstractmethod
    def has_model(self, ref: ModelRef) -> bool:
        """provider/model 是否存在于目录中。"""

    @abstractmethod
    def get(self, ref: ModelRef) -> ModelCatalogEntry:
        """返回元数据；未知模型抛 ModelBadRequestError（gateway 归一化错误）。"""
