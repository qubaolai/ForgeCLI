"""模型选择解析器 ModelSelectionResolver 契约（ADR-0011 §2 / §3.4）。

ModelSelectionResolver 负责把「当前模型」或「显式模型选择」解析为具体 provider/model；
按用途的显式覆盖在这里读取。解析结果始终经 ModelCatalogService 的存在性与能力校验。

依赖方向（§2）：

    LlmGateway -> ModelSelectionResolver -> ModelCatalogService   （均只读）

语义约束：
    - 未被显式覆盖的用途一律走当前主模型；origin 只作用途标签，不选择模型。
    - current_model 与 explicit_model 都不做模型 fallback，只允许同一 provider/model 的
      凭证级重试；LLM 不能提出模型升级或切换。

"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from forgecli.application.llm.gateway.catalog import ModelCatalogEntry
from forgecli.application.llm.gateway.origin import RequestOrigin
from forgecli.application.llm.gateway.selection import ModelSelection
from forgecli.application.llm.model_ref import ModelRef


@dataclass(frozen=True)
class ResolvedModel:
    """解析输出：已解析的 provider/model 及其目录条目。"""

    ref: ModelRef
    entry: ModelCatalogEntry


class ModelSelectionResolver(ABC):
    """把 ModelSelection 解析为具体模型的只读解析器。"""

    @abstractmethod
    def resolve(
        self,
        selection: ModelSelection,
        *,
        origin: RequestOrigin,
        required_capabilities: tuple[str, ...] = (),
        min_context_window: int | None = None,
    ) -> ResolvedModel:
        """解析 selection -> ResolvedModel，并经 ModelCatalogService 校验存在性与能力。

        入参取自 ModelRequest 的相关字段（origin / required_capabilities /
        min_context_window）。current_model 与 explicit_model 均不做模型 fallback；
        解析失败时归一化为 gateway 错误（如 ModelBadRequestError 等）。
        """
