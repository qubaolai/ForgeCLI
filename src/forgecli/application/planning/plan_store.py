"""计划与待办的存储抽象 (ADR-0022 §8).

只声明"读什么写什么", 不声明"落在哪". 实现在 infrastructure/planning.

**并发**: 项目级 ProcessLock 已保证同一项目同时只有一个 Forge 进程, 因此这里不设文件锁.
写入本身仍要原子 (先写同目录临时文件再 rename) —— 防的是崩溃, 不是并发.

读失败一律降级成"没有计划", 不抛异常: 计划不可用不该阻塞主流程 (ADR-0022 §10). 写失败
必须让调用方知道, 因为模型以为自己存下来了.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.planning import PlanDocument, PlanIndex, TodoList

__all__ = ["PlanStore", "PlanStoreError"]


class PlanStoreError(RuntimeError):
    """写入失败. 读失败不用它 —— 那条路降级成空值."""


class PlanStore(ABC):
    """一个项目的计划与待办存储."""

    @abstractmethod
    def load_index(self) -> PlanIndex:
        """活动指针与全部计划摘要. 文件缺失或损坏时返回空索引."""

    @abstractmethod
    def save_index(self, index: PlanIndex) -> None: ...

    @abstractmethod
    def load_plan(
        self, plan_id: str, revision: int | None = None
    ) -> PlanDocument | None:
        """读某份计划. revision 为空时读最新的一版; 读不到返回 None."""

    @abstractmethod
    def save_plan(self, plan: PlanDocument, rendered: str) -> None:
        """写一个 revision.

        rendered 由调用方渲染后传入, 而不是让 store 自己调模板: 渲染规则属 application,
        存储只负责把两份内容放到该放的位置.
        """

    @abstractmethod
    def load_todo(self) -> TodoList | None:
        """当前生效的待办清单; 没有或损坏时返回 None."""

    @abstractmethod
    def save_todo(self, todo: TodoList) -> None: ...

    @abstractmethod
    def archive_todo(self, todo: TodoList) -> None:
        """把旧清单挪进归档. 归档不删文件 —— 换一份清单不该让上一份消失."""
