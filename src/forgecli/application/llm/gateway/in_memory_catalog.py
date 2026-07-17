"""内存态模型目录 InMemoryModelCatalog（ADR-0011 §4）。

ModelCatalogService 的最小具体实现：由一组 ModelCatalogEntry 在内存里索引，供
ModelSelectionResolver 做存在性与能力前置校验。它*不解析任何配置*——真实的「内置
baseline 与用户 `[providers.*.models.*]` TOML 合并」的运行时视图留给后续切片；本类是那个
加载器最终要填充的内存缝，也让 gateway / resolver 现在就能端到端跑通。

单模型能力的唯一事实来源仍是这里的 ModelCatalogEntry（§4）：provider/model 存在性、
context window、allowlist、supports_* 都从条目读，调用方不各自复述 provider 能力。
"""

from __future__ import annotations

from collections.abc import Iterable

from forgecli.application.llm.gateway.catalog import (
    ModelCatalogEntry,
    ModelCatalogService,
)
from forgecli.application.llm.gateway.errors import ModelBadRequestError
from forgecli.application.llm.model_ref import ModelRef


class InMemoryModelCatalog(ModelCatalogService):
    """由固定 entries 支撑的只读目录视图；不解析配置，键为 (provider, model)。"""

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
