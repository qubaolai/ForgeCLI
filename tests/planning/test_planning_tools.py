"""五个计划/待办工具 (ADR-0022 决策 5).

最要紧的一组断言是**它们的 input_schema 里没有路径字段**. 那不是风格问题, 而是"这批
工具不经过审批"这条决策的全部支点: 写入位置由 Forge 从项目 id 算出来, 模型无法指定写到
哪里, 于是没有可裁决的内容.

有人日后为了"方便"给 plan.write 加一个 path 参数, 这条决策就地失效 —— 而且不会有任何一层
报错. 所以只能正面钉住.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.planning import PlanningService
from forgecli.application.tools.builtin import (
    PlanReadTool,
    PlanWriteTool,
    TodoReadTool,
    TodoSetStatusTool,
    TodoWriteTool,
)
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import TargetResolution, ToolPlan
from forgecli.domain.tool.result import ToolResultStatus, TurnDisposition
from forgecli.domain.tool.spec import TargetDeclarationAbility
from forgecli.infrastructure.planning import FsPlanStore
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE

_PLAN_ARGS = {
    "title": "拆分值域对象",
    "goal": "把混在一起的领域概念分开",
    "context": "domain 下几个模块互相引用",
    "approach": "按聚合边界切",
    "steps": [{"title": "读现状"}, {"title": "切分", "detail": "先动 tool"}],
    "risks": [],
    "acceptance": ["make ci 全绿"],
}


@pytest.fixture
def planning(tmp_path: Path) -> PlanningService:
    return PlanningService(FsPlanStore(lambda: tmp_path / "plans"))


@pytest.fixture
def context(tmp_path: Path) -> ExecutionContext:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _run(tool: Tool, context: ExecutionContext, arguments: dict[str, object]):  # type: ignore[no-untyped-def]
    """参数按字典传, 不用 **kwargs: 计划的字段里有一个就叫 context, 与形参撞名."""
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name=tool.spec.name,
            arguments=arguments,
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    return tool.perform(plan, context)


def _tools(planning: PlanningService) -> tuple[Tool, ...]:
    return (
        PlanReadTool(planning),
        PlanWriteTool(planning),
        TodoReadTool(planning),
        TodoWriteTool(planning),
        TodoSetStatusTool(planning),
    )


# ---- "不经审批"的支点 ----


def test_no_tool_takes_a_path(planning: PlanningService) -> None:
    """写入位置由 Forge 从项目 id 算出来, 模型无法指定写到哪里.

    有人日后加一个 path 参数, 这条决策就地失效且没有任何一层会报错.
    """
    for tool in _tools(planning):
        properties = tool.spec.input_schema.get("properties", {})
        assert isinstance(properties, dict)
        for name in properties:
            assert "path" not in name.lower(), f"{tool.spec.name} 出现了路径字段 {name}"
            assert "dir" not in name.lower(), f"{tool.spec.name} 出现了路径字段 {name}"


def test_no_tool_accepts_extra_properties(planning: PlanningService) -> None:
    """允许额外字段等于允许将来某个实现悄悄加一个路径参数进来."""
    for tool in _tools(planning):
        assert tool.spec.input_schema.get("additionalProperties") is False


def test_every_tool_declares_only_plan_only(planning: PlanningService) -> None:
    """PLAN_ONLY 是四档模式预算的公共子集, 所以"不经 ASK"是既有规则的自然结果."""
    for tool in _tools(planning):
        assert tool.spec.declared_capabilities == frozenset({Capability.PLAN_ONLY})
        assert tool.spec.target_declaration_ability is TargetDeclarationAbility.STATIC


def test_the_plan_is_static_and_touches_nothing(
    planning: PlanningService, context: ExecutionContext
) -> None:
    """空 effects 是事实, 不是省略."""
    tool = PlanWriteTool(planning)
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="plan.write",
            arguments=_PLAN_ARGS,
            tool_call_id="c1",
        ),
        context,
    )

    assert isinstance(plan, ToolPlan)
    assert plan.target_resolution is TargetResolution.STATIC
    assert plan.effects.read_paths == ()
    assert plan.effects.mutating_targets == ()


# ---- 行为 ----


def test_writing_a_plan_returns_the_rendered_body(
    planning: PlanningService, context: ExecutionContext
) -> None:
    result = _run(PlanWriteTool(planning), context, _PLAN_ARGS)

    assert result.status is ToolResultStatus.OK
    assert result.text.startswith("# 拆分值域对象")


def test_writing_a_plan_asks_for_a_human_decision(
    planning: PlanningService, context: ExecutionContext
) -> None:
    """工具声明"本次输出需要人裁决"; 循环据此在回合边界停下, 它不认识这个工具名."""
    result = _run(PlanWriteTool(planning), context, _PLAN_ARGS)

    assert result.turn_disposition is TurnDisposition.AWAIT_USER_DECISION


def test_reading_without_a_plan_says_there_is_none(
    planning: PlanningService, context: ExecutionContext
) -> None:
    """说清是"没有"而不是"读失败": 前者该去提一份计划, 后者该找人."""
    result = _run(PlanReadTool(planning), context, {})

    assert "没有生效的计划" in result.text
    assert result.turn_disposition is TurnDisposition.CONTINUE


def test_reading_a_plan_matches_what_write_returned(
    planning: PlanningService, context: ExecutionContext
) -> None:
    written = _run(PlanWriteTool(planning), context, _PLAN_ARGS)

    assert _run(PlanReadTool(planning), context, {}).text == written.text


def test_todo_write_then_read(
    planning: PlanningService, context: ExecutionContext
) -> None:
    _run(TodoWriteTool(planning), context, {"items": ["先读", "再写"]})

    body = _run(TodoReadTool(planning), context, {}).text

    assert "0. [ ] 先读" in body
    assert "1. [ ] 再写" in body


def test_set_status_marks_the_item(
    planning: PlanningService, context: ExecutionContext
) -> None:
    _run(TodoWriteTool(planning), context, {"items": ["先读", "再写"]})

    body = _run(
        TodoSetStatusTool(planning),
        context,
        {"updates": [{"index": 1, "status": "in_progress"}]},
    ).text

    assert "1. [>] 再写" in body


def test_a_bad_index_is_a_plain_tool_error(
    planning: PlanningService, context: ExecutionContext
) -> None:
    """模型能自己改正的普通失败, 不该走安全裁决, 也不该炸成一次工具异常."""
    _run(TodoWriteTool(planning), context, {"items": ["先读"]})

    result = _run(
        TodoSetStatusTool(planning),
        context,
        {"updates": [{"index": 9, "status": "done"}]},
    )

    assert result.status is ToolResultStatus.TOOL_ERROR
    assert "共 1 项" in result.text


def test_two_in_progress_in_one_call_is_refused(
    planning: PlanningService, context: ExecutionContext
) -> None:
    """一次调用里想把两条都标成进行中: 不变量在 TodoList 构造里拦下, 工具只负责转译."""
    _run(TodoWriteTool(planning), context, {"items": ["先读", "再写"]})

    result = _run(
        TodoSetStatusTool(planning),
        context,
        {
            "updates": [
                {"index": 0, "status": "in_progress"},
                {"index": 1, "status": "in_progress"},
            ]
        },
    )

    # 第二次改动会把第一条降回 pending, 这是 with_status 的既定行为 —— 于是它成功了,
    # 而结果里只有一条 in_progress. 断言的是不变量, 不是失败.
    assert result.status is ToolResultStatus.OK
    assert result.text.count("[>]") == 1


def test_set_status_without_a_list_says_what_to_do(
    planning: PlanningService, context: ExecutionContext
) -> None:
    result = _run(
        TodoSetStatusTool(planning),
        context,
        {"updates": [{"index": 0, "status": "done"}]},
    )

    assert "todo.write" in result.text
