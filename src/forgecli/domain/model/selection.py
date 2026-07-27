"""模型选择 ModelSelection 密封层级（ADR-0011 §3.4）。

模型选择必须显式表达，避免 current_model 与 provider/model 同时出现时产生歧义。
两个子类型把「字段互斥」做成*结构上不可表达*：

    CurrentModelSelection   用当前主模型，不携带 provider/model，不做 fallback。
    ExplicitModelSelection  显式 provider/model（按用途覆盖），不做模型 fallback。

系统不维护 fast / smart 之类的模型档位，也不按用途自动切换模型（ADR-0011 §决策）。
两种选择都只允许同一 provider/model 的凭证级重试，均不做模型 fallback。

LLM 不能直接指定 kind / provider / model，也不能提出模型升级或切换；它只产出内容，
模型由用户配置（当前模型）与可选的按用途显式覆盖决定，最终由 AgentLoop /
ModelSelectionResolver 裁决（§3.4）。gateway 仍要校验字段组合——这里把组合校验前移到
类型层面，gateway 只需按 kind 分发。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class SelectionKind(Enum):
    """选择判别枚举，便于日志、分发与测试断言。"""

    CURRENT_MODEL = "current_model"
    EXPLICIT_MODEL = "explicit_model"


@dataclass(frozen=True)
class ModelSelection(ABC):
    """模型选择值对象基类。不直接实例化，使用下方两个子类型之一。"""

    @property
    @abstractmethod
    def kind(self) -> SelectionKind: ...


@dataclass(frozen=True)
class CurrentModelSelection(ModelSelection):
    """使用项目 / session 的当前主模型；不携带任何 provider / model 字段。"""

    @property
    def kind(self) -> SelectionKind:
        return SelectionKind.CURRENT_MODEL


@dataclass(frozen=True)
class ExplicitModelSelection(ModelSelection):
    """使用显式指定的 provider/model（按用途显式覆盖）。不做跨模型 fallback。"""

    provider: str
    model: str

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("ExplicitModelSelection 的 provider 与 model 都不能为空")

    @property
    def kind(self) -> SelectionKind:
        return SelectionKind.EXPLICIT_MODEL
