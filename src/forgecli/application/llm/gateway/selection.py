"""模型选择 ModelSelection 密封层级（ADR-0011 §3.4）。

模型选择必须显式表达，避免 current_model、tier、provider/model 同时出现时产生歧义。
三个子类型把「字段互斥」做成结构上不可表达：

    CurrentModelSelection   只表示「用当前主模型」，不携带 tier / provider / model /
                            allow_fallback / fallback_policy（§3.4；不做 fallback）。
    TierModelSelection      必须携带 tier；在候选列表内按策略选择，可按策略 fallback。
    ExplicitModelSelection  必须携带 provider/model；默认不跨模型 fallback。

LLM 不能直接指定 kind / tier / provider / model；它只能产出能力诉求，最终由
AgentLoop / AgentTurnService 裁决（§3.4 末尾）。gateway 仍要校验字段组合——这里把
组合校验前移到类型层面，gateway 只需按 kind 分发。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class SelectionKind(Enum):
    """选择判别枚举，便于日志、分发与测试断言。"""

    CURRENT_MODEL = "current_model"
    TIER = "tier"
    EXPLICIT_MODEL = "explicit_model"


@dataclass(frozen=True)
class ModelSelection(ABC):
    """模型选择值对象基类。不直接实例化，使用下方三个子类型之一。"""

    @property
    @abstractmethod
    def kind(self) -> SelectionKind: ...


@dataclass(frozen=True)
class CurrentModelSelection(ModelSelection):
    """使用项目 / session 的当前主模型；不携带任何档位或显式引用字段。"""

    @property
    def kind(self) -> SelectionKind:
        return SelectionKind.CURRENT_MODEL


@dataclass(frozen=True)
class TierModelSelection(ModelSelection):
    """使用系统用途档位（fast / smart / default 等）。"""

    tier: str
    allow_fallback: bool = True
    fallback_policy: str | None = None

    def __post_init__(self) -> None:
        if not self.tier.strip():
            raise ValueError("TierModelSelection.tier 不能为空")

    @property
    def kind(self) -> SelectionKind:
        return SelectionKind.TIER


@dataclass(frozen=True)
class ExplicitModelSelection(ModelSelection):
    """使用显式指定的 provider/model。默认不跨模型 fallback。"""

    provider: str
    model: str
    allow_fallback: bool = False
    fallback_policy: str | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("ExplicitModelSelection 的 provider 与 model 都不能为空")

    @property
    def kind(self) -> SelectionKind:
        return SelectionKind.EXPLICIT_MODEL
