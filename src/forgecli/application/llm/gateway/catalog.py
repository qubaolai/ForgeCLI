"""模型目录的读端口（ADR-0011 §4）。

条目本身（ModelCatalogEntry）住在 domain.model.catalog；这里只留取数抽象，
具体实现（内存 / 配置构建 / thinking 覆盖层）在 application 与 infrastructure。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef


class ModelCatalogService(ABC):
    """单模型能力的唯一只读事实来源。今日仅冻结接口，不实现具体视图。"""

    @abstractmethod
    def has_model(self, ref: ModelRef) -> bool:
        """provider/model 是否存在于目录中。"""

    @abstractmethod
    def get(self, ref: ModelRef) -> ModelCatalogEntry:
        """返回元数据；未知模型抛 ModelBadRequestError（gateway 归一化错误）。"""
