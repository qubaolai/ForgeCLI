"""待办清单的纯值对象 (ADR-0022 §4).

与计划的分工:

| | 计划 | 待办 |
| --- | --- | --- |
| 回答 | 整个大方向 | 当前该做哪一步 |
| 谁裁决 | 人 (ADR-0023) | 无需裁决 |
| 批准之后 | 不再随执行变动, 要变就是新 revision | 允许随时纠正 |

**待办不必绑定计划** (ADR-0022 决策 4): `plan_id` 可空. 绝大多数中等任务直接从一段需求
生成待办, 不经过计划评审.

核心不变量是"同一时刻最多一条 in_progress", 它放在构造校验里而不是某个服务方法里 ——
任何调用方都绕不过去. 允许多条并行等于没有"当前步骤", 而"当前步骤"正是这份清单存在的
全部理由.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

__all__ = [
    "MAX_TODO_ITEMS",
    "TodoItem",
    "TodoList",
    "TodoStatus",
]

MAX_TODO_ITEMS = 50


class TodoStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    # 做到一半发现不需要了. 与 done 分开: 它不该被算进"完成了几项".
    DROPPED = "dropped"

    @property
    def settled(self) -> bool:
        """不会再变的终态."""
        return self in (TodoStatus.DONE, TodoStatus.DROPPED)


@dataclass(frozen=True)
class TodoItem:
    title: str
    status: TodoStatus = TodoStatus.PENDING

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("TodoItem.title 不能为空")


@dataclass(frozen=True)
class TodoList:
    """当前生效的待办清单."""

    todo_id: str
    items: tuple[TodoItem, ...] = ()
    plan_id: str = ""
    revision: int = 1
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.todo_id.strip():
            raise ValueError("TodoList.todo_id 不能为空")
        if len(self.items) > MAX_TODO_ITEMS:
            raise ValueError(
                f"TodoList.items 至多 {MAX_TODO_ITEMS} 项, 收到 {len(self.items)}"
            )
        running = [item for item in self.items if item.status is TodoStatus.IN_PROGRESS]
        if len(running) > 1:
            titles = ", ".join(item.title for item in running)
            raise ValueError(
                "同一时刻最多一条 in_progress, 收到 "
                f"{len(running)} 条: {titles}. 先把其余几条改回 pending."
            )

    # ---- 派生 ----

    @property
    def done_count(self) -> int:
        return sum(1 for item in self.items if item.status is TodoStatus.DONE)

    @property
    def total_count(self) -> int:
        """dropped 不计入分母: 一份"3/5"里那两条被放弃的步骤会让人以为还有活没干."""
        return sum(1 for item in self.items if item.status is not TodoStatus.DROPPED)

    @property
    def current(self) -> TodoItem | None:
        for item in self.items:
            if item.status is TodoStatus.IN_PROGRESS:
                return item
        return None

    @property
    def finished(self) -> bool:
        return bool(self.items) and all(item.status.settled for item in self.items)

    # ---- 状态推进 ----

    def with_status(self, index: int, status: TodoStatus) -> TodoList:
        """改一项的状态. index 从 0 起.

        把某项改成 in_progress 时, **原来那条自动降回 pending**. 不这么做的话构造校验会
        直接抛错, 而模型面对"先把上一条改掉再改这一条"只会多跑一轮 —— 它想表达的意思
        非常明确, 没必要让它分两步说.
        """
        if not 0 <= index < len(self.items):
            raise ValueError(
                f"待办项序号超出范围: {index}, 当前共 {len(self.items)} 项 (从 0 起)"
            )
        items = list(self.items)
        if status is TodoStatus.IN_PROGRESS:
            items = [
                replace(item, status=TodoStatus.PENDING)
                if item.status is TodoStatus.IN_PROGRESS
                else item
                for item in items
            ]
        items[index] = replace(items[index], status=status)
        return replace(self, items=tuple(items), revision=self.revision + 1)

    def render(self) -> str:
        """给模型与终端看的清单正文. 序号从 0 起, 与 todo.set_status 的入参一致."""
        if not self.items:
            return "(待办清单为空)"
        marks = {
            TodoStatus.PENDING: "[ ]",
            TodoStatus.IN_PROGRESS: "[>]",
            TodoStatus.DONE: "[x]",
            TodoStatus.DROPPED: "[-]",
        }
        lines = [
            f"{index}. {marks[item.status]} {item.title}"
            for index, item in enumerate(self.items)
        ]
        return "\n".join(lines)
