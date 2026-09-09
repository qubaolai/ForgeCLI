"""当前状态帧的渲染: 请求的第 [6] 层 (ADR-0041).

计划, 待办与跨会话记忆. 三者原先是系统提示词尾部的三个块, 而整个 system prompt 排在
messages 之前 —— 一次 `todo_write` 就让前面几十轮对话全部退出前缀缓存. 搬到请求末尾之后,
变的只有它自己那几百 token.

**它不进窗口, 也不落事件.** 只在组装期生成, 于是下一轮的窗口里没有上一轮的状态帧, 公共
前缀一直延伸到上一轮最后一条消息为止.

空的是常态: 新会话没有计划, 没有待办, 也没有记忆. 三节都空时返回空串, 调用方据此整个不
渲染这一帧 —— 而不是发一个写着"(无)"的空壳.

## 帧内的正文要转义

这一帧由 Forge 组装, 却在协议上走一条 user 消息, 所以它自己带分隔行标出来. 而帧里的
**待办标题与记忆值都是模型写的** —— 不转义的话, 模型 (或者影响了它的一段工具输出) 只要
写一条含 `--- END CURRENT STATE ---` 的记忆, 此后每一轮的状态帧都会在那里提前收尾, 其后
的文字看起来就落在了围栏之外, 且身处 user 消息里. 记忆是静默写入不经人确认的
(ADR-0033 决策 2), 所以这条路一旦通就是持久的.

与 ADR-0018 §5.3 给 FORGE.md 定的是同一道防线, 共用同一个转义器.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from forgecli.application.planning.planning_service import ActivePlanning
from forgecli.application.prompt.sentinels import (
    STATE_BEGIN,
    STATE_END,
    escape_sentinels,
)
from forgecli.application.prompt.template_renderer import render_state
from forgecli.domain.memory.entry import MemoryEntry, MemoryScope
from forgecli.domain.planning.plan import PlanDocument
from forgecli.domain.planning.todo import TodoList

__all__ = ["render_state_frame"]


@dataclass(frozen=True)
class _PlanView:
    plan_id: str
    title: str
    status: str
    step_count: int


@dataclass(frozen=True)
class _TodoView:
    rendered: str
    done: int
    total: int


@dataclass(frozen=True)
class _MemoryGroup:
    """记忆按分级分组后的一组. scope 用取值而不是枚举: 模板按它查标签."""

    scope: str
    entries: tuple[MemoryEntry, ...]


def render_state_frame(
    planning: ActivePlanning, memory: tuple[MemoryEntry, ...]
) -> str:
    """渲染这一轮的状态帧. 三节都空时返回空串."""
    plan = _plan_view(planning.live_plan)
    todo = _todo_view(planning.live_todo)
    groups = _memory_groups(memory)
    if plan is None and todo is None and not groups:
        return ""
    # 加载提示词模板: frame.md.j2
    return render_state("frame", plan=plan, todo=todo, memory_groups=groups)


def _safe(text: str) -> str:
    """帧内一切非 Forge 撰写的正文都要过这一道."""
    return escape_sentinels(text, STATE_BEGIN, STATE_END)


def _plan_view(plan: PlanDocument | None) -> _PlanView | None:
    """没有计划, 或者它已经做完了, 都整节不渲染.

    后一种同样不渲染 —— 一份做完的计划每轮注入只会让模型去想它和当前这件事有没有关系.
    """
    if plan is None:
        return None
    return _PlanView(
        plan_id=plan.plan_id,
        title=_safe(plan.title),
        status=plan.status.value,
        step_count=plan.step_count,
    )


def _todo_view(todo: TodoList | None) -> _TodoView | None:
    if todo is None or not todo.items:
        return None
    return _TodoView(
        rendered=_safe(todo.render()), done=todo.done_count, total=todo.total_count
    )


def _memory_groups(memory: tuple[MemoryEntry, ...]) -> tuple[_MemoryGroup, ...]:
    """按分级分组. 顺序取自枚举声明顺序, 不另立一份分级清单.

    集合的迭代顺序不稳定会让同样的输入产出不同的文本, 从而毁掉这一帧的确定性; 枚举的
    声明顺序是稳定的.
    """
    groups = []
    for scope in MemoryScope:
        entries = tuple(
            replace(entry, key=_safe(entry.key), value=_safe(entry.value))
            for entry in memory
            if entry.scope is scope
        )
        if entries:
            groups.append(_MemoryGroup(scope=scope.value, entries=entries))
    return tuple(groups)
