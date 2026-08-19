"""fs.read_file: 读一个文件 (ADR-0004 §14).

上界是 WORKSPACE_READ + EXTERNAL_READ, 但**每次调用只声明其中一个**: 读工作区内文件是
WORKSPACE_READ, 读工作区外文件是 EXTERNAL_READ. 这个区分是整套解耦的关键示例 —— 安全
模块不认识 "fs.read_file" 这个名字, 它只看到"这次要读区外路径", 于是按 mode 预算落
ASK. 读 ~/.ssh/id_rsa 和读 src/main.py 因此得到不同待遇, 而工具本身没有一行策略代码.
"""

from __future__ import annotations

from types import MappingProxyType

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import (
    emit_text,
    read_capability,
    resolve_target,
    validate_arguments,
)
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathKind
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    PlanEffects,
    TargetResolution,
    ToolPlan,
)
from forgecli.domain.tool.result import (
    ToolMetrics,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import (
    ArtifactPolicy,
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["ReadFileTool"]

_SPEC = ToolSpec(
    name="fs.read_file",
    version="1",
    title="读取文件",
    description="读取工作区内某个文件的文本内容. 超长内容会截断并落为产物.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "max_bytes": {"type": "integer"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_READ, Capability.EXTERNAL_READ}
    ),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=10.0,
    artifact_policy=ArtifactPolicy(max_inline_bytes=16 * 1024),
)


class ReadFileTool(Tool):
    def __init__(
        self,
        governor: ResourceGovernor,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        self._governor = governor
        self._artifacts = artifacts

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(_SPEC, request.arguments)
        if invalid is not None:
            return invalid
        target = resolve_target(request.arguments.get("path"), context)
        if isinstance(target, PreparationError):
            return target
        absolute, facts = target
        if facts.kind is not PathKind.FILE:
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"不是普通文件: {absolute}",
                field_path="path",
            )
        # 用 realpath 判定归属: 工作区里一个指向 /etc 的软链接, 字面路径看着在区内.
        scope = context.scope_of(absolute)
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "path": facts.realpath,
                    "max_bytes": request.arguments.get("max_bytes"),
                }
            ),
            capabilities=frozenset({read_capability(scope)}),
            effects=PlanEffects(read_paths=(facts.realpath,)),
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
        limits = self._governor.limits_for(_SPEC)
        path = str(plan.normalized_input["path"])
        text = context.filesystem.read_text(path, max_bytes=limits.max_artifact_bytes)
        emitted = emit_text(
            text,
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="read_file",
        )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=emitted.parts,
            artifacts=emitted.artifacts,
            metrics=ToolMetrics(bytes_out=emitted.bytes_out),
        )
