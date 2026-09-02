"""ToolCatalog: 由外部谓词算出的工具目录视图 (ADR-0004 §7).

注册表不认识 mode. 能力门是 application 层构造的一个谓词, 注册表只负责过滤和快照 ——
这样"哪些工具在 plan 档可见"这条策略不会渗进工具系统, 也不会散落到各个工具实现里.

catalog_snapshot_hash 进模型请求记录, 事件和授权信封: 目录一变就能追溯, 避免"模型看到
的工具集"与"审计记录的工具集"对不上.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.spec import ToolSpec
from forgecli.domain.tool.tool_call import ToolSchema

__all__ = ["CatalogQuery", "ToolCatalog"]


@dataclass(frozen=True)
class CatalogQuery:
    """一次目录查询. reason_tag 说明这次过滤为什么这么做, 进快照哈希与审计."""

    predicate: Callable[[ToolSpec], bool]
    reason_tag: str

    def __post_init__(self) -> None:
        if not self.reason_tag.strip():
            raise ValueError("CatalogQuery.reason_tag 不能为空")


@dataclass(frozen=True)
class ToolCatalog:
    """一次过滤的结果快照. entries 按 name 排序, 保证快照哈希稳定."""

    entries: tuple[ToolSpec, ...]
    reason_tag: str
    catalog_snapshot_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.entries, key=lambda spec: spec.name))
        object.__setattr__(self, "entries", ordered)
        object.__setattr__(
            self,
            "catalog_snapshot_hash",
            digest(
                {
                    "reason_tag": self.reason_tag,
                    "entries": [spec.spec_hash for spec in ordered],
                }
            ),
        )

    def find(self, name: str) -> ToolSpec | None:
        return next((spec for spec in self.entries if spec.name == name), None)

    def contains(self, name: str) -> bool:
        return self.find(name) is not None

    def to_model_schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(spec.to_model_schema() for spec in self.entries)
