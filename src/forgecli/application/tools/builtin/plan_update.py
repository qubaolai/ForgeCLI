"""plan.update: 维护当前任务计划 (ADR-0013 §1).

PLAN_ONLY 能力的唯一持有者. 它不产生任何外部副作用, 也不能间接调用别的工具 —— 计划只
存在进程内, 由本工具持有. 这是 plan 档下模型唯一能"做点什么"的出口.

计划状态跟着工具实例走 (一个会话一个实例). 不落盘: 计划是本轮工作的草稿, 落盘就得回答
"resume 时该不该恢复一份可能已经过时的计划", 而它本来就该由模型重新说一遍.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from forgecli.application.tools.builtin.base import validate_arguments
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.domain.tool.result import ContentPart, ToolResult, ToolResultStatus
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["PlanStep", "PlanUpdateTool"]

_STATUSES = ("pending", "in_progress", "done", "dropped")

_SPEC = ToolSpec(
    name="plan.update",
    version="1",
    title="更新任务计划",
    description="创建或更新当前任务的步骤清单. 只影响计划本身, 不执行任何外部动作.",
    input_schema={
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "status": {"type": "string", "enum": list(_STATUSES)},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            },
            "note": {"type": "string"},
        },
        "required": ["steps"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"plan": {"type": "string"}}},
    declared_capabilities=frozenset({Capability.PLAN_ONLY}),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=5.0,
)


@dataclass(frozen=True)
class PlanStep:
    title: str
    status: str = "pending"


class PlanUpdateTool(Tool):
    def __init__(self) -> None:
        self._steps: tuple[PlanStep, ...] = ()
        self._note = ""

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    @property
    def steps(self) -> tuple[PlanStep, ...]:
        return self._steps

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(_SPEC, request.arguments)
        if invalid is not None:
            return invalid
        raw_steps = request.arguments.get("steps", [])
        if not isinstance(raw_steps, list) or not raw_steps:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="steps 至少要有一项",
                field_path="steps",
            )
        normalized = [
            {
                "title": str(item.get("title", "")).strip(),
                "status": str(item.get("status", "pending")),
            }
            for item in raw_steps
            if isinstance(item, dict)
        ]
        if any(not step["title"] for step in normalized):
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="步骤标题不能为空",
                field_path="steps",
            )
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {"steps": normalized, "note": str(request.arguments.get("note", ""))}
            ),
            capabilities=frozenset({Capability.PLAN_ONLY}),
            effects=PlanEffects(),
            target_resolution=TargetResolution.STATIC,
            workspace_scope=WorkspaceScope.IN_WORKSPACE,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        raw = plan.normalized_input.get("steps", [])
        steps = raw if isinstance(raw, list) else []
        self._steps = tuple(
            PlanStep(title=str(item["title"]), status=str(item["status"]))
            for item in steps
            if isinstance(item, dict)
        )
        self._note = str(plan.normalized_input.get("note", ""))
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text=self.render()),),
        )

    def render(self) -> str:
        lines = [f"[{step.status}] {step.title}" for step in self._steps]
        if self._note:
            lines.append(f"note: {self._note}")
        return "\n".join(lines) or "(空计划)"
