"""plan_read / plan_write / TodoWrite / todo_set_status (ADR-0022 / ADR-0023).

没有路径字段, 写哪由 PlanningService 算: 缺省 allow. plan_write 交出计划之后本轮停下等
用户裁决 (ToolResult.await_user_decision).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.tool.names import (
    PLAN_READ_TOOL_NAME,
    PLAN_WRITE_TOOL_NAME,
    TODO_SET_STATUS_TOOL_NAME,
    TODO_WRITE_TOOL_NAME,
)
from forgecli.core.tool.tool import (
    CanUseToolFn,
    ProgressCallback,
    PromptOptions,
    Tool,
    ToolResult,
    ToolUseContext,
    ValidationResult,
)
from forgecli.planning.plan import MAX_PLAN_STEPS, PlanStep
from forgecli.planning.service import PlanningService, StatusUpdate, is_safe_plan_id
from forgecli.planning.todo import MAX_TODO_ITEMS, TodoStatus
from forgecli.tools.builtin.base import str_of, text_block
from forgecli.tools.builtin.planning.arguments import index_of, mappings_of, strings_of
from forgecli.tools.builtin.planning.prompt import (
    PLAN_READ_PROMPT,
    PLAN_WRITE_PROMPT,
    TODO_SET_STATUS_PROMPT,
    TODO_WRITE_PROMPT,
)

__all__ = [
    "PlanReadTool",
    "PlanWriteTool",
    "PlanningOutput",
    "TodoSetStatusTool",
    "TodoWriteTool",
]

_TODO_STATUSES = tuple(status.value for status in TodoStatus)


@dataclass(frozen=True)
class PlanningOutput:
    text: str
    is_error: bool = False


def _schema(
    properties: Mapping[str, object], required: tuple[str, ...]
) -> Mapping[str, object]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


class _PlanningTool(Tool):
    max_result_size_chars = 100_000

    def __init__(self, planning: PlanningService) -> None:
        self._planning = planning

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, PlanningOutput):
            return text_block(tool_use_id, str(output))
        return text_block(tool_use_id, output.text, is_error=output.is_error)


class PlanReadTool(_PlanningTool):
    name = PLAN_READ_TOOL_NAME
    search_hint = "read the active plan"
    input_schema = _schema({"plan_id": {"type": "string"}}, ())

    def description(self, input: Mapping[str, object]) -> str:
        return "Read the plan"

    def prompt(self, options: PromptOptions) -> str:
        return PLAN_READ_PROMPT

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        return True

    def is_concurrency_safe(self, input: Mapping[str, object]) -> bool:
        return True

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        return "Reading the plan"

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        plan_id = str_of(input, "plan_id")
        if plan_id and not is_safe_plan_id(plan_id):
            return ValidationResult(
                ok=False,
                message=(
                    "plan_id may only contain word characters and hyphens, not paths"
                ),
                error_code=1,
            )
        return ValidationResult(ok=True)

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        body = self._planning.read_plan(str_of(input, "plan_id"))
        if body is None:
            body = (
                "There is no active plan. Use plan_write when the direction needs "
                "agreement first."
            )
        return ToolResult(data=PlanningOutput(body))


class PlanWriteTool(_PlanningTool):
    name = PLAN_WRITE_TOOL_NAME
    search_hint = "submit a plan for review"
    input_schema = _schema(
        {
            "name": {
                "type": "string",
                "maxLength": 60,
                "description": (
                    "Short name for this plan, used as its directory name and index "
                    "label; name it after the task, e.g. web-shutdown-fix. Only used "
                    "when creating; ignored when submitting a new version."
                ),
            },
            "title": {"type": "string"},
            "goal": {"type": "string"},
            "context": {"type": "string"},
            "approach": {"type": "string"},
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_PLAN_STEPS,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            },
            "risks": {"type": "array", "items": {"type": "string"}},
            "acceptance": {"type": "array", "items": {"type": "string"}},
            "plan_id": {"type": "string"},
        },
        (
            "name",
            "title",
            "goal",
            "context",
            "approach",
            "steps",
            "risks",
            "acceptance",
        ),
    )

    def description(self, input: Mapping[str, object]) -> str:
        return "Submit a plan"

    def prompt(self, options: PromptOptions) -> str:
        return PLAN_WRITE_PROMPT

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        return f"Submitting plan {str_of(input, 'title')}".strip()

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        plan_id = str_of(input, "plan_id")
        if not plan_id:
            return ValidationResult(ok=True)
        if not is_safe_plan_id(plan_id):
            return ValidationResult(
                ok=False,
                message=(
                    "plan_id may only contain word characters and hyphens, not paths"
                ),
                error_code=1,
            )
        if self._planning.read_plan(plan_id) is None:
            return ValidationResult(
                ok=False,
                message=f"Cannot revise a plan that does not exist: {plan_id}",
                error_code=2,
            )
        return ValidationResult(ok=True)

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        steps: list[PlanStep] = []
        for item in mappings_of(input.get("steps")):
            steps.append(
                PlanStep(
                    title=str(item.get("title", "")), detail=str(item.get("detail", ""))
                )
            )
        document = self._planning.write_plan(
            name=str_of(input, "name"),
            title=str_of(input, "title"),
            goal=str_of(input, "goal"),
            context=str_of(input, "context"),
            approach=str_of(input, "approach"),
            steps=steps,
            risks=strings_of(input.get("risks")),
            acceptance=strings_of(input.get("acceptance")),
            plan_id=str_of(input, "plan_id"),
        )
        body = self._planning.read_plan(document.plan_id) or ""
        return ToolResult(data=PlanningOutput(body), await_user_decision=True)


class TodoWriteTool(_PlanningTool):
    name = TODO_WRITE_TOOL_NAME
    aliases = ("todo_write",)
    search_hint = "create or rewrite the todo list"
    input_schema = _schema(
        {
            "name": {
                "type": "string",
                "maxLength": 60,
                "description": (
                    "Short name for this list, named after the task. Reusing the "
                    "current list's name means correcting the same list; a different "
                    "name means this is a list for another task and the old one is "
                    "archived."
                ),
            },
            "items": {
                "type": "array",
                "maxItems": MAX_TODO_ITEMS,
                "items": {"type": "string"},
            },
        },
        ("name", "items"),
    )

    def description(self, input: Mapping[str, object]) -> str:
        return "Update the todo list"

    def prompt(self, options: PromptOptions) -> str:
        return TODO_WRITE_PROMPT

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        return "Updating todos"

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        todo = self._planning.write_todo(
            strings_of(input.get("items")), name=str_of(input, "name")
        )
        return ToolResult(data=PlanningOutput(todo.render()))


class TodoSetStatusTool(_PlanningTool):
    name = TODO_SET_STATUS_TOOL_NAME
    search_hint = "change todo item statuses"
    input_schema = _schema(
        {
            "updates": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "minimum": 0},
                        "status": {"type": "string", "enum": list(_TODO_STATUSES)},
                    },
                    "required": ["index", "status"],
                    "additionalProperties": False,
                },
            }
        },
        ("updates",),
    )

    def description(self, input: Mapping[str, object]) -> str:
        return "Update todo statuses"

    def prompt(self, options: PromptOptions) -> str:
        return TODO_SET_STATUS_PROMPT

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        return "Updating todo statuses"

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        updates: list[StatusUpdate] = []
        for item in mappings_of(input.get("updates")):
            updates.append(
                StatusUpdate(
                    index=index_of(item.get("index")),
                    status=TodoStatus(str(item.get("status", ""))),
                )
            )
        try:
            todo = self._planning.update_status(updates)
        except ValueError as exc:
            return ToolResult(data=PlanningOutput(str(exc), True))
        if todo is None:
            return ToolResult(
                data=PlanningOutput(
                    "There is no todo list yet. Create one with TodoWrite first."
                )
            )
        return ToolResult(data=PlanningOutput(todo.render()))
