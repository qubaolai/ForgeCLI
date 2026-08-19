"""计划的纯值对象 (ADR-0022 §4).

计划回答的是**整个大方向**: 目标, 方案, 步骤骨架, 风险, 验收. 它由人裁决 (ADR-0023),
批准之后不再随执行变动 —— 要变就是新的 revision.

与待办的分工写在 [todo.py] 的模块说明里. 一句话: 计划是"要去哪", 待办是"现在走到哪".

这里不引入 Path, TOML 库或任何 I/O: 渲染规则属 application, 文件读写属 infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "MAX_PLAN_STEPS",
    "PlanDocument",
    "PlanIndex",
    "PlanStatus",
    "PlanStep",
    "PlanSummary",
]

# 步骤上限. 不是性能考虑 —— 一份 20 步以上的计划已经不是"大方向"而是任务分解, 那种粒度
# 属于待办清单, 而待办清单有自己的上限.
MAX_PLAN_STEPS = 20


class PlanStatus(Enum):
    """计划的生命周期. 迁移由人的裁决驱动 (ADR-0023 §决策 3), 不由执行驱动."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    # 用户要求补充之后, 旧 revision 变成这个状态: 它既不是被否决, 也不再生效.
    SUPERSEDED = "superseded"

    @property
    def active(self) -> bool:
        """还在等人裁决或已经生效. 归档的两种状态不算."""
        return self in (PlanStatus.PROPOSED, PlanStatus.APPROVED)


@dataclass(frozen=True)
class PlanStep:
    """一条计划步骤.

    **不带 status** —— 与 `docs/02-detailed-design.md` §3.4 的刻意偏离 (ADR-0022 §4.1).
    状态跟踪整体归待办清单: 两边都挂 status 会立刻产生"以谁为准"的问题, 而那正是旧
    `plan.update` 失败的方式.

    也不带 `requires_approval`: 审批是逐次工具调用的事 (ADR-0004 §6.1), 不是步骤的属性
    —— 一条步骤可能展开成十次工具调用, 每次各自裁决.
    """

    title: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("PlanStep.title 不能为空")


@dataclass(frozen=True)
class PlanDocument:
    """一份计划的某个 revision.

    frozen: 状态推进用 `dataclasses.replace` 产生新值, 与 SessionSnapshot 一致. 计划一旦
    被人批准, 那一份就是历史记录, 原地改它等于篡改被批准的内容.
    """

    plan_id: str
    revision: int
    title: str
    goal: str
    context: str = ""
    approach: str = ""
    steps: tuple[PlanStep, ...] = ()
    risks: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    status: PlanStatus = PlanStatus.PROPOSED
    template_version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise ValueError("PlanDocument.plan_id 不能为空")
        if self.revision < 1:
            raise ValueError("PlanDocument.revision 从 1 起")
        if not self.title.strip():
            raise ValueError("PlanDocument.title 不能为空")
        if not self.goal.strip():
            raise ValueError("PlanDocument.goal 不能为空")
        if not self.steps:
            raise ValueError("PlanDocument.steps 至少要有一项")
        if len(self.steps) > MAX_PLAN_STEPS:
            raise ValueError(
                f"PlanDocument.steps 至多 {MAX_PLAN_STEPS} 项, 收到 {len(self.steps)}"
            )

    @property
    def step_count(self) -> int:
        return len(self.steps)


@dataclass(frozen=True)
class PlanSummary:
    """索引里的一条计划摘要 (ADR-0022 §2.2).

    只有摘要, 正文在各自的文件里 —— 索引里再复制一份正文就有了两个会漂的副本.
    """

    plan_id: str
    revision: int
    status: PlanStatus
    title: str
    template_version: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class PlanIndex:
    """活动指针 + 全部计划摘要.

    活动指针放在这里而**不放进 SessionSnapshot** (ADR-0022 §6.1): 计划是项目级状态, 一份
    计划可能跨三个会话, 指针跟着项目走而不是跟着会话走.
    """

    active_plan_id: str = ""
    active_todo_id: str = ""
    plans: tuple[PlanSummary, ...] = field(default=())

    def summary_of(self, plan_id: str) -> PlanSummary | None:
        for item in self.plans:
            if item.plan_id == plan_id:
                return item
        return None

    @property
    def active(self) -> PlanSummary | None:
        if not self.active_plan_id:
            return None
        return self.summary_of(self.active_plan_id)
