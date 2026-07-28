"""模型选择的解析端口（ADR-0011 §3.1）。

结果值对象 ResolvedModel 住在 domain.model.resolved；这里只留端口，具体解析策略
（读配置 / 校验能力 / 不做模型 fallback）在 application 实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.resolved import ResolvedModel
from forgecli.domain.model.selection import ModelSelection


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
