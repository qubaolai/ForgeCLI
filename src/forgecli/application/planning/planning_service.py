"""计划与待办的用例 (ADR-0022 §5).

一句话职责: 它是**唯一**知道"当前生效的计划和待办是哪一份"的地方. 工具, 斜杠命令与
会话加载都从这里取, 谁都不自己去翻目录.

三条边界:

- **加载是只读的.** 打开一个会话不会把 PROPOSED 变成 APPROVED, 也不会重置待办状态.
  状态只由工具与人的裁决改变 (ADR-0022 §5.2).
- **计划与待办归属产生它们的那个会话.** 目录按 session 分区 (见 paths.plans_dir), 因此
  新会话天然什么都读不到. 跨会话带出一份计划等于带出一份没有上下文的清单 —— 模型会按
  自己的理解填空, 而那个理解未必是当初的.
- **不可用不阻塞.** 索引缺失, 文件损坏, 版本比当前新 —— 一律降级成"没有计划", 打诊断,
  照常开工 (§10).
- **不发事件.** 会话事件与运行事件由调用方 (AgentTurnService) 发, 因为只有它持有
  turn_id, 而这里不认识 turn.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from forgecli.application.planning.plan_store import PlanStore
from forgecli.application.planning.plan_template import (
    PLAN_TEMPLATE_VERSION,
    render_plan,
)
from forgecli.domain.planning import (
    PlanDocument,
    PlanIndex,
    PlanStatus,
    PlanStep,
    PlanSummary,
    TodoItem,
    TodoList,
    TodoStatus,
)

__all__ = ["ActivePlanning", "PlanningService", "StatusUpdate"]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_plan_id() -> str:
    """短 id, **不由标题派生** —— 标题会改, 而 id 是文件名的一部分."""
    return f"pl_{secrets.token_hex(3)}"


def _new_todo_id() -> str:
    return f"td_{secrets.token_hex(3)}"


@dataclass(frozen=True)
class ActivePlanning:
    """一次加载的结果. 两项都可能为空 —— 那是常态, 不是错误."""

    plan: PlanDocument | None = None
    todo: TodoList | None = None
    # 加载过程中出的问题, 给终端打一行诊断用. 不抛异常: 计划坏了不该让人开不了工.
    diagnostics: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return self.plan is None and self.todo is None

    @property
    def live_plan(self) -> PlanDocument | None:
        """还有事要做的计划, 否则 None.

        判据一句话: **它还等着人裁决, 或者它播种的待办还没做完.**

        跨会话的陈旧问题已经由目录分区解决 (新会话什么都读不到), 这条只管**会话内**的
        一种: 一件事做完了, 用户接着提下一件, 而上一份计划还挂在那儿.

        proposed 单独留一条路, 是因为那种计划**还没有待办** —— 它正等着人拍板. 按"没待办
        就不显示"处理会把它藏掉, 而那恰恰是最该让人看见的一份.

        推导而不是改盘: 加载必须是只读的 (§5.2), 而且文件记录要留给 /plan list.
        """
        plan = self.plan
        if plan is None:
            return None
        if plan.status is PlanStatus.PROPOSED:
            return plan
        todo = self.todo
        if todo is None or todo.plan_id != plan.plan_id:
            # 批准过, 但它播种的那份待办已经被别的清单顶掉了. 执行跟踪没了, 这份计划也就
            # 无从判断做没做完 —— 按做完处理, 方向是少显示一份陈旧的东西.
            return None
        return None if todo.finished else plan

    @property
    def live_todo(self) -> TodoList | None:
        """还没做完的待办, 否则 None.

        全部落终态之后整块不再渲染. 留一份 "5/5 完成" 每轮注入, 对模型是噪音, 对下一件
        事还是误导 —— 它会去想这两件事有没有关系.
        """
        todo = self.todo
        if todo is None or not todo.items or todo.finished:
            return None
        return todo


@dataclass(frozen=True)
class StatusUpdate:
    """一次状态改动. index 从 0 起, 与 TodoList.render 的编号一致."""

    index: int
    status: TodoStatus


class PlanningService:
    def __init__(
        self,
        store: PlanStore,
        *,
        clock: Callable[[], str] = _now_iso,
        plan_id_factory: Callable[[], str] = _new_plan_id,
        todo_id_factory: Callable[[], str] = _new_todo_id,
    ) -> None:
        self._store = store
        self._clock = clock
        self._new_plan_id = plan_id_factory
        self._new_todo_id = todo_id_factory

    # ---- 加载 ----

    def load(self) -> ActivePlanning:
        """读当前活动的计划与待办. 任何一步失败都降级, 不抛."""
        diagnostics: list[str] = []
        index = self._store.load_index()
        plan: PlanDocument | None = None
        if index.active_plan_id:
            plan = self._store.load_plan(index.active_plan_id)
            if plan is None:
                diagnostics.append(
                    f"索引指向的计划 {index.active_plan_id} 读不到, 已按没有计划处理"
                )
            elif plan.template_version > PLAN_TEMPLATE_VERSION:
                # 比当前实现更新的模板: 渲染出来大概率缺小节, 与其给人看一份半截的,
                # 不如说清楚.
                diagnostics.append(
                    f"计划 {plan.plan_id} 的模板版本 {plan.template_version} "
                    f"高于当前支持的 {PLAN_TEMPLATE_VERSION}, 已按没有计划处理"
                )
                plan = None
        todo = self._store.load_todo()
        return ActivePlanning(plan=plan, todo=todo, diagnostics=tuple(diagnostics))

    def load_index(self) -> PlanIndex:
        """给 /plan list 用. 索引里只有摘要, 正文各在各的文件里."""
        return self._store.load_index()

    def set_active_plan(self, plan_id: str) -> bool:
        """切换活动计划. 计划不存在时返回 False —— 指针指向一份读不到的计划, 下次会话
        开头就会打一条"读不到"的诊断, 而用户根本不知道自己指错了."""
        if self._store.load_plan(plan_id) is None:
            return False
        self._store.save_index(
            replace(self._store.load_index(), active_plan_id=plan_id)
        )
        return True

    # ---- 计划 ----

    def read_plan(self, plan_id: str = "") -> str | None:
        """渲染后的计划正文. 不传 plan_id 时读当前活动的那一份.

        返回 Markdown 而不是结构体: 模型该看到的与人看到的是同一份 (ADR-0022 §2.1).
        """
        target = plan_id or self._store.load_index().active_plan_id
        if not target:
            return None
        plan = self._store.load_plan(target)
        return render_plan(plan) if plan is not None else None

    def write_plan(
        self,
        *,
        title: str,
        goal: str,
        context: str,
        approach: str,
        steps: Sequence[PlanStep],
        risks: Sequence[str],
        acceptance: Sequence[str],
        plan_id: str = "",
    ) -> PlanDocument:
        """提交一份新计划, 或已有计划的下一个 revision.

        给了 plan_id 就是续写: revision + 1, 同一个 plan_id 下. 于是一次"补充 -> 重提"的
        往返留在同一份计划的历史里, 而不是散成两份互不相干的计划.
        """
        now = self._clock()
        index = self._store.load_index()
        previous = self._store.load_plan(plan_id) if plan_id else None
        document = PlanDocument(
            plan_id=plan_id or self._new_plan_id(),
            revision=(previous.revision + 1) if previous is not None else 1,
            title=title,
            goal=goal,
            context=context,
            approach=approach,
            steps=tuple(steps),
            risks=tuple(risks),
            acceptance=tuple(acceptance),
            status=PlanStatus.PROPOSED,
            template_version=PLAN_TEMPLATE_VERSION,
            created_at=previous.created_at if previous is not None else now,
            updated_at=now,
        )
        self._store.save_plan(document, render_plan(document))
        self._store.save_index(_with_summary(index, document, active=True))
        return document

    def set_plan_status(self, plan_id: str, status: PlanStatus) -> PlanDocument | None:
        """推进计划状态. 由人的裁决驱动 (ADR-0023), 不由执行驱动."""
        plan = self._store.load_plan(plan_id)
        if plan is None:
            return None
        updated = replace(plan, status=status, updated_at=self._clock())
        self._store.save_plan(updated, render_plan(updated))
        index = self._store.load_index()
        # 被否决或被取代的计划不再是活动计划: 让指针继续指着它, 下次会话开头就会显示一份
        # 已经作废的计划.
        active = status.active
        self._store.save_index(_with_summary(index, updated, active=active))
        return updated

    # ---- 待办 ----

    def read_todo(self) -> TodoList | None:
        return self._store.load_todo()

    def write_todo(self, titles: Sequence[str], *, plan_id: str = "") -> TodoList:
        """整表替换 (内容纠错).

        旧清单进归档而不是被覆盖: 换一份清单不该让上一份消失, 事后要能回答"当时那份是
        什么样".

        **状态一律重置为 pending.** 整表替换表达的是"步骤拆错了", 而不是"步骤没变但我要
        改状态" —— 后者是 set_status 的事. 试图在这里保留状态需要把新旧两张表按标题配对,
        而标题恰恰是这次要改的东西.
        """
        existing = self._store.load_todo()
        if existing is not None:
            self._store.archive_todo(existing)
        todo = TodoList(
            todo_id=existing.todo_id if existing is not None else self._new_todo_id(),
            items=tuple(TodoItem(title=title) for title in titles),
            plan_id=plan_id or (existing.plan_id if existing is not None else ""),
            revision=(existing.revision + 1) if existing is not None else 1,
            updated_at=self._clock(),
        )
        self._store.save_todo(todo)
        self._store.save_index(
            replace(self._store.load_index(), active_todo_id=todo.todo_id)
        )
        return todo

    def update_status(self, updates: Sequence[StatusUpdate]) -> TodoList | None:
        """改一项或多项的状态. 改不动步骤集合 —— 那由 schema 保证, 不靠模型自觉."""
        todo = self._store.load_todo()
        if todo is None:
            return None
        for update in updates:
            todo = todo.with_status(update.index, update.status)
        todo = replace(todo, updated_at=self._clock())
        self._store.save_todo(todo)
        return todo

    def seed_from_plan(self, plan: PlanDocument) -> TodoList:
        """用计划的步骤播种待办 (ADR-0022 决策 3).

        批准一份计划 = 用它的步骤播种待办. 这一步让"计划 -> 执行"之间不需要人再翻译一次,
        也不需要模型重新把步骤抄一遍 —— 抄的过程正是步骤悄悄走样的地方.
        """
        return self.write_todo(
            [step.title for step in plan.steps], plan_id=plan.plan_id
        )


def _with_summary(index: PlanIndex, plan: PlanDocument, *, active: bool) -> PlanIndex:
    summary = PlanSummary(
        plan_id=plan.plan_id,
        revision=plan.revision,
        status=plan.status,
        title=plan.title,
        template_version=plan.template_version,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )
    others = tuple(item for item in index.plans if item.plan_id != plan.plan_id)
    active_id = plan.plan_id if active else ""
    if not active and index.active_plan_id != plan.plan_id:
        # 别人是活动计划就别动指针: 否决 A 不该顺手把 B 的活动状态也清掉.
        active_id = index.active_plan_id
    return replace(index, active_plan_id=active_id, plans=(*others, summary))
