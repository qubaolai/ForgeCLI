"""fs.move: 移动或重命名一个工作区文件 (ADR-0004 §14).

为什么要有它, 而不是让模型去写 `shell.run mv`:

`MovePair` 与 `Capability.PATH_MOVE` 是恢复层与规则引擎的一等概念 —— 移动要同时校验
源和目标两端, 冲突域要把两端都算进去, 撤销要把文件搬回原处. 走 shell 的话, 安全侧只
看到一条 `mv a b` 命令, 目标集合是 UNKNOWN, 恢复层拿不到 MovePair, 撤销只能靠 FULL
checkpoint 兜底. 有专用工具, 这些事实在 prepare 阶段就是封闭的.
"""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType

from forgecli.application.tools.builtin.base import validate_arguments
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathKind
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    MovePair,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.domain.tool.result import (
    ContentPart,
    ToolError,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["MoveTool"]

_SPEC = ToolSpec(
    name="fs.move",
    version="1",
    title="移动或重命名文件",
    description=(
        "把一个工作区文件移动到新路径 (重命名同理). 目标已存在时失败, "
        "不会静默覆盖. 移动前会记录两端以便撤销."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "target": {"type": "string"},
        },
        "required": ["source", "target"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"target": {"type": "string"}}},
    declared_capabilities=frozenset({Capability.PATH_MOVE, Capability.EXTERNAL_WRITE}),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=15.0,
)


class MoveTool(Tool):
    def __init__(self, mover: Callable[[str, str], None]) -> None:
        self._move = mover

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(_SPEC, request.arguments)
        if invalid is not None:
            return invalid
        source = self._path(request, "source", context)
        if isinstance(source, PreparationError):
            return source
        target = self._path(request, "target", context)
        if isinstance(target, PreparationError):
            return target

        source_facts = context.filesystem.facts(source)
        if not source_facts.exists:
            return PreparationError(
                code=PreparationErrorCode.TARGET_NOT_FOUND,
                message=f"源路径不存在: {source}",
                field_path="source",
            )
        if source_facts.kind is not PathKind.FILE:
            # 目录移动要把整棵树的两端都算进冲突域与恢复清单, 不在本工具范围内.
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"只支持移动普通文件: {source}",
                field_path="source",
            )
        if context.filesystem.facts(target).exists:
            # 不静默覆盖: 覆盖是两次写 (删目标 + 写目标), 与"移动"是两回事, 恢复层
            # 存的 preimage 也不一样. 要覆盖请先显式删除.
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=f"目标已存在, 不会覆盖: {target}",
                field_path="target",
            )

        real_source = source_facts.realpath or source
        scope = context.scope_for_write_all((real_source, target))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {"source": real_source, "target": target}
            ),
            capabilities=frozenset({_capability(scope)}),
            effects=PlanEffects(
                move_pairs=(MovePair(source=real_source, target=target),)
            ),
            target_resolution=TargetResolution.STATIC,
            workspace_scope=scope,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        source = str(plan.normalized_input["source"])
        target = str(plan.normalized_input["target"])
        try:
            self._move(source, target)
        except OSError as exc:
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=_SPEC.name,
                status=ToolResultStatus.TOOL_ERROR,
                error=ToolError(code="move_failed", message=str(exc)),
            )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text=f"已移动 {source} -> {target}"),),
        )

    @staticmethod
    def _path(
        request: ToolInvocationRequest, field: str, context: ExecutionContext
    ) -> str | PreparationError:
        raw = request.arguments.get(field)
        if not isinstance(raw, str) or not raw.strip():
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=f"{field} 必须是非空字符串",
                field_path=field,
            )
        return context.resolve(raw)


def _capability(scope: WorkspaceScope) -> Capability:
    return (
        Capability.EXTERNAL_WRITE
        if scope is WorkspaceScope.OUTSIDE
        else Capability.PATH_MOVE
    )
