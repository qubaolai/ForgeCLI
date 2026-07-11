"""模型目录只读视图 ModelCatalogService（ADR-0011 §4）。

ModelCatalogService 是单模型能力的唯一只读事实来源：provider/model 存在性、
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

from forgecli.application.llm.model_ref import ModelRef


@dataclass(frozen=True)
class ModelCatalogEntry:
    """单模型的只读元数据边界（ADR-0011 §4）。

    provider/model 唯一定位一个模型；能力位与窗口供 ModelSelectionResolver 做能力前置
    校验（后续切片）。价格等其余元数据按需在后续切片扩展，本处先定义能力与窗口。
    """

    provider: str
    model: str
    context_window: int
    max_output_tokens: int | None = None
    supports_structured_output: bool = False
    supports_tool_calling: bool = False
    supports_thinking: bool = False
    allowlisted: bool = True  # 允许列出
    deprecated: bool = False  # 废弃

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("ModelCatalogEntry 的 provider 与 model 都不能为空")
        if self.context_window <= 0:
            raise ValueError("ModelCatalogEntry.context_window 必须为正整数")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("ModelCatalogEntry.max_output_tokens 必须为正整数")


class ModelCatalogService(ABC):
    """单模型能力的唯一只读事实来源。目前仅实现接口，不实现具体服务。"""

    @abstractmethod
    def has_model(self, ref: ModelRef) -> bool:
        """provider/model 是否存在于目录中。"""

    @abstractmethod
    def get(self, ref: ModelRef) -> ModelCatalogEntry:
        """返回元数据；未知模型抛 ModelBadRequestError（gateway 归一化错误）。"""
