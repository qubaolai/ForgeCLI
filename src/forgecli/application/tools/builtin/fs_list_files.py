"""fs.list_files: 按 glob 列出文件 (ADR-0004 §14).

target_declaration_ability=expandable: 工具自己理解 glob 的语义, 因此在 prepare 里就把
它展开成完整的绝对路径集合并输出 FORGE_EXPANDED. 这正是 ADR-0004 §4 说的"谁能证明目标
集合, 谁就负责冻结它" —— 不把 `src/**/*.py` 原样丢给安全模块让它猜.

展开用的是 ExecutionContext 里已经冻结的文件系统视图, 与执行时同一份.
"""

from __future__ import annotations

from types import MappingProxyType

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import (
    emit_text,
    filter_globbed,
    joined,
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
from forgecli.domain.tool.result import ToolResult, ToolResultStatus
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["ListFilesTool"]

_MAX_ENTRIES = 2000

_SPEC = ToolSpec(
    name="fs.list_files",
    version="2",
    title="列出文件",
    description=(
        "列出目录下的文件与子目录. pattern 是 glob, 相对给定目录展开. "
        "默认 '*' 只列当前一层; 加 '**/' 前缀递归整棵树, "
        "例如 pattern='**/*.java' 列出所有 Java 文件, "
        "pattern='**/application*.yml' 按文件名找文件. "
        "depth 可再限制层数. "
        "默认跳过 .git, node_modules, target 一类生成目录, "
        "需要它们时传 include_ignored=true."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "pattern": {"type": "string"},
            "depth": {"type": "integer", "minimum": 1, "maximum": 32},
            "include_ignored": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"entries": {"type": "array"}}},
    declared_capabilities=frozenset({Capability.WORKSPACE_READ}),
    target_declaration_ability=TargetDeclarationAbility.EXPANDABLE,
    default_timeout_seconds=15.0,
)


def _empty_message(root: str, pattern: str) -> str:
    return (
        f"目录 {root} 下没有匹配 {pattern} 的文件. "
        "已按默认规则忽略 .git, node_modules, __pycache__ 一类目录; "
        "需要它们请传 include_ignored=true. 目录存在且可读, 这是确定的空结果."
    )


class ListFilesTool(Tool):
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
        raw_path = request.arguments.get("path", context.primary_root)
        target = resolve_target(raw_path, context)
        if isinstance(target, PreparationError):
            return target
        root, facts = target
        if facts.kind is not PathKind.DIRECTORY:
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"不是目录: {root}",
                field_path="path",
            )
        pattern = str(request.arguments.get("pattern", "*"))
        raw_depth = request.arguments.get("depth")
        depth = raw_depth if isinstance(raw_depth, int) and raw_depth > 0 else None
        include_ignored = bool(request.arguments.get("include_ignored", False))
        matches = filter_globbed(
            context.filesystem.expand_glob(pattern, root=facts.realpath),
            root=facts.realpath,
            depth=depth,
            include_ignored=include_ignored,
        )[:_MAX_ENTRIES]
        scope = context.scope_of_all((facts.realpath, *matches))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "path": facts.realpath,
                    "pattern": pattern,
                    "depth": depth,
                    "include_ignored": include_ignored,
                    "entries": list(matches),
                }
            ),
            capabilities=frozenset({read_capability(scope)}),
            effects=PlanEffects(read_paths=matches or (facts.realpath,)),
            # 目标集合已由 Forge 用冻结视图展开并封闭.
            target_resolution=TargetResolution.FORGE_EXPANDED,
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
        entries = plan.normalized_input.get("entries", [])
        listed = [str(item) for item in entries] if isinstance(entries, list) else []
        root = str(plan.normalized_input["path"])
        pattern = str(plan.normalized_input["pattern"])
        limits = self._governor.limits_for(_SPEC)
        parts, artifacts = emit_text(
            # 空结果要说清"确实没有"而不是只回一句"(无匹配)": 后者与"参数写错了"
            # 长得一样, 模型只能换个写法再试一次, 而目录真空时换多少次都一样.
            joined(listed) or _empty_message(root, pattern),
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="list_files",
        )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=parts,
            artifacts=artifacts,
        )
