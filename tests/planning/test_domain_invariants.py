"""计划与待办的构造不变量 (ADR-0022 §4).

这些校验放在构造函数里而不是某个服务方法里, 理由是**调用方绕不过去**. 一条写在服务里
的校验只保护经过那个服务的路径, 而反序列化, 测试替身与将来的第二个调用方都不经过它.
"""

from __future__ import annotations

import pytest

from forgecli.domain.planning import (
    MAX_PLAN_STEPS,
    MAX_TODO_ITEMS,
    PlanDocument,
    PlanStep,
    TodoItem,
    TodoList,
    TodoStatus,
)


def _plan(**overrides: object) -> PlanDocument:
    base: dict[str, object] = {
        "plan_id": "pl_1",
        "revision": 1,
        "title": "拆分值域对象",
        "goal": "把混在一起的领域概念分开",
        "steps": (PlanStep(title="先读现状"),),
    }
    base.update(overrides)
    return PlanDocument(**base)  # type: ignore[arg-type]


def _todo(*statuses: TodoStatus) -> TodoList:
    return TodoList(
        todo_id="td_1",
        items=tuple(
            TodoItem(title=f"第 {index} 步", status=status)
            for index, status in enumerate(statuses)
        ),
    )


# ---- 计划 ----


def test_a_plan_needs_at_least_one_step() -> None:
    """没有步骤的计划没法播种待办, 而播种正是计划与执行之间那道桥."""
    with pytest.raises(ValueError, match="至少要有一项"):
        _plan(steps=())


def test_a_plan_caps_its_steps() -> None:
    """20 步以上已经不是"大方向"而是任务分解, 那个粒度属于待办清单."""
    steps = tuple(PlanStep(title=f"s{i}") for i in range(MAX_PLAN_STEPS + 1))
    with pytest.raises(ValueError, match="至多"):
        _plan(steps=steps)


def test_title_and_goal_cannot_be_blank() -> None:
    with pytest.raises(ValueError, match="title"):
        _plan(title="   ")
    with pytest.raises(ValueError, match="goal"):
        _plan(goal="")


def test_revisions_start_at_one() -> None:
    with pytest.raises(ValueError, match="从 1 起"):
        _plan(revision=0)


def test_a_step_needs_a_title() -> None:
    with pytest.raises(ValueError, match="title"):
        PlanStep(title=" ")


# ---- 待办: 最多一条 in_progress ----


def test_two_in_progress_items_are_rejected() -> None:
    """允许多条并行等于没有"当前步骤", 而那正是这份清单存在的全部理由."""
    with pytest.raises(ValueError, match="最多一条 in_progress"):
        _todo(TodoStatus.IN_PROGRESS, TodoStatus.IN_PROGRESS)


def test_the_error_says_which_items_conflict() -> None:
    """报错要能直接照着改, 否则模型只能重写整张表 —— 那正是要避免的高风险操作."""
    with pytest.raises(ValueError) as caught:
        _todo(TodoStatus.IN_PROGRESS, TodoStatus.IN_PROGRESS)

    assert "第 0 步" in str(caught.value)
    assert "第 1 步" in str(caught.value)


def test_one_in_progress_is_fine() -> None:
    todo = _todo(TodoStatus.DONE, TodoStatus.IN_PROGRESS, TodoStatus.PENDING)

    assert todo.current is not None
    assert todo.current.title == "第 1 步"


def test_moving_the_pointer_demotes_the_previous_item() -> None:
    """模型想说的是"现在做第 2 条", 意思很明确, 没必要让它分两步说."""
    todo = _todo(TodoStatus.IN_PROGRESS, TodoStatus.PENDING)

    moved = todo.with_status(1, TodoStatus.IN_PROGRESS)

    assert moved.items[0].status is TodoStatus.PENDING
    assert moved.items[1].status is TodoStatus.IN_PROGRESS


def test_a_status_change_bumps_the_revision() -> None:
    todo = _todo(TodoStatus.PENDING)

    assert todo.with_status(0, TodoStatus.DONE).revision == todo.revision + 1


def test_an_out_of_range_index_says_how_many_there_are() -> None:
    with pytest.raises(ValueError, match="共 1 项"):
        _todo(TodoStatus.PENDING).with_status(5, TodoStatus.DONE)


def test_the_list_caps_its_items() -> None:
    with pytest.raises(ValueError, match="至多"):
        TodoList(
            todo_id="td_1",
            items=tuple(TodoItem(title=f"s{i}") for i in range(MAX_TODO_ITEMS + 1)),
        )


# ---- 进度口径 ----


def test_dropped_items_leave_the_denominator() -> None:
    """一份 "3/5" 里那两条被放弃的步骤会让人以为还有活没干."""
    todo = _todo(
        TodoStatus.DONE, TodoStatus.DONE, TodoStatus.DROPPED, TodoStatus.PENDING
    )

    assert (todo.done_count, todo.total_count) == (2, 3)


def test_a_list_of_settled_items_is_finished() -> None:
    assert _todo(TodoStatus.DONE, TodoStatus.DROPPED).finished


def test_an_empty_list_is_not_finished() -> None:
    """空清单是"还没开始", 不是"全干完了"."""
    assert not TodoList(todo_id="td_1").finished


def test_rendering_numbers_from_zero_to_match_the_tool_input() -> None:
    """序号与 todo_set_status 的入参必须同一套. 差一位的清单比没有清单更危险."""
    rendered = _todo(TodoStatus.DONE, TodoStatus.IN_PROGRESS).render()

    assert rendered.splitlines()[0].startswith("0. [x]")
    assert rendered.splitlines()[1].startswith("1. [>]")
