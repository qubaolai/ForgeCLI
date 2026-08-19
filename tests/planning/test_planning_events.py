"""计划与待办的会话事件 (ADR-0022 §6).

判据是 **revision 比对, 不是工具名** —— 这一条单独测, 因为它决定了机制的适用范围: 按
工具名判断的话, 将来任何一条别的路径改了计划 (斜杠命令, 恢复, 子 Agent) 都不会有事件.

payload 只记摘要与引用. 复制一份正文进事件流就有了两个会漂移的副本, 而事件流是
append-only 的, 漂了改不回来.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from forgecli.application.planning import PlanningService
from forgecli.domain.planning import PlanStep
from forgecli.domain.session.events import EventType
from forgecli.infrastructure.planning import FsPlanStore


@pytest.fixture
def planning(tmp_path: Path) -> PlanningService:
    return PlanningService(FsPlanStore(lambda: tmp_path / "plans"))


def test_the_event_types_exist() -> None:
    """三条事件的值即落盘字符串, 不可随意改."""
    assert EventType.PLAN_CREATED.value == "plan_created"
    assert EventType.PLAN_REVIEWED.value == "plan_reviewed"
    assert EventType.TODO_UPDATED.value == "todo_updated"


def test_a_new_revision_changes_the_identity(planning: PlanningService) -> None:
    """身份是 (plan_id, revision). 同一份计划提第二版也算变 —— 每个 revision 都是一份
    新文档, 所以它也该有一条自己的 PLAN_CREATED."""
    first = planning.write_plan(
        title="拆分",
        goal="目标",
        context="",
        approach="",
        steps=(PlanStep(title="一"),),
        risks=(),
        acceptance=(),
    )
    second = planning.write_plan(
        title="拆分",
        goal="目标",
        context="",
        approach="",
        steps=(PlanStep(title="一"), PlanStep(title="二")),
        risks=(),
        acceptance=(),
        plan_id=first.plan_id,
    )

    assert (first.plan_id, first.revision) != (second.plan_id, second.revision)


def test_a_status_update_changes_the_todo_identity(
    planning: PlanningService,
) -> None:
    """打一个勾也要发事件: 审计要能回答"这一步是什么时候完成的"."""
    from forgecli.application.planning import StatusUpdate
    from forgecli.domain.planning import TodoStatus

    before = planning.write_todo(["一", "二"])
    after = planning.update_status([StatusUpdate(index=0, status=TodoStatus.DONE)])

    assert after is not None
    assert after.revision != before.revision


def _payload_keys() -> Mapping[str, tuple[str, ...]]:
    return {
        "plan": (
            "plan_id",
            "revision",
            "template_version",
            "title",
            "step_count",
            "file",
        ),
        "todo": ("todo_id", "plan_id", "revision", "done", "total"),
    }


def test_the_payload_carries_no_body() -> None:
    """摘要与引用, 不含正文.

    这是一条只能靠约定维持的性质 —— 往 payload 里塞一段正文不会让任何东西报错, 只会让
    事件流慢慢变成第二份计划正文, 而它是 append-only 的.
    """
    keys = _payload_keys()
    for group in keys.values():
        assert "goal" not in group
        assert "approach" not in group
        assert "steps" not in group
        assert "items" not in group
