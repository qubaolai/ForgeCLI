"""选择解析的结果值对象（ADR-0011 §3.1）。

ResolvedModel 是一对已确定的事实: 用哪个 ModelRef, 它的目录条目长什么样。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef


@dataclass(frozen=True)
class ResolvedModel:
    """解析输出：已解析的 provider/model 及其目录条目。"""

    ref: ModelRef
    entry: ModelCatalogEntry
