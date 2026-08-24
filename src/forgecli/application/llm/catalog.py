"""模型目录: 只读端口 + 内存实现 (ADR-0011 §4).

目录是 llm 切片的**共享词汇**, 不是调用切片的内部构件. 消费它的有四方: 配置服务
(写 llm.json 前先合并校验), thinking 覆盖层, 计量的 CostEstimator, 以及选择解析.
其中只有最后一个属于 gateway.

ADR-0028: 它原先住在 gateway/ 里, 于是 catalog_builder 这个**上游生产方**要反过来
import 下游包 —— 加上 gateway/__init__ 的急切转导出, 就成了一个真的 import 环
(见 selection.py 的说明). 端口与内存实现合成一个文件: 两者各二十行, 拆开只是让
读的人多跳一次.

条目本身 (ModelCatalogEntry) 住在 domain.model.catalog; 这里不解析任何配置, 由
catalog_builder 把用户的 `[llm.providers.*.models.*]` 转成条目后填入.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from forgecli.application.llm.gateway.errors import ModelBadRequestError
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef


class ModelCatalogService(ABC):
    """单模型能力的唯一只读事实来源。"""

    @abstractmethod
    def has_model(self, ref: ModelRef) -> bool:
        """provider/model 是否存在于目录中。"""

    @abstractmethod
    def get(self, ref: ModelRef) -> ModelCatalogEntry:
        """返回元数据；未知模型抛 ModelBadRequestError（gateway 归一化错误）。"""


class InMemoryModelCatalog(ModelCatalogService):
    """由固定 entries 支撑的只读目录视图；不解析配置，键为 (provider, model)。

    单模型能力的唯一事实来源仍是这里的 ModelCatalogEntry（§4）：provider/model
    存在性、context window、allowlist、supports_* 都从条目读，调用方不各自复述
    provider 能力。
    """

    def __init__(self, entries: Iterable[ModelCatalogEntry] = ()) -> None:
        self._by_ref: dict[ModelRef, ModelCatalogEntry] = {}
        for entry in entries:
            ref = ModelRef(provider=entry.provider, model=entry.model)
            if ref in self._by_ref:
                raise ValueError(f"模型目录重复条目: {ref}")
            self._by_ref[ref] = entry

    def has_model(self, ref: ModelRef) -> bool:
        return ref in self._by_ref

    def get(self, ref: ModelRef) -> ModelCatalogEntry:
        entry = self._by_ref.get(ref)
        if entry is None:
            # 未知模型（目录只含已知 provider 条目）归一为 bad request，
            # 由 resolver / gateway 直接上抛，绝不 fallback 到别的模型。
            raise ModelBadRequestError(
                f"未知模型 {ref}", provider=ref.provider, model=ref.model
            )
        return entry
