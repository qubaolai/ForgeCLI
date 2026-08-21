"""fs.scan_tree: 一次看清目录结构 (ADR-0004 §14).

与 fs.list_files 的分工不是重复:

- `fs.list_files` 回答"哪些路径匹配这个 glob" —— 一个**平铺**的路径清单, 前提是已经
  知道要找什么.
- `fs.scan_tree` 回答"这个目录长什么样" —— 带层级的骨架, 用在还不知道要找什么的时候.

把后者硬用前者来做, 模型只能 `**/*` 一把梭, 拿回几千行绝对路径, 每行重复一遍相同的
前缀; 上下文被冲掉, 而"哪里是源码, 哪里是测试"这个真正的问题仍然没有答案. 所以这里
渲染的是相对路径的缩进树, 深到头的目录只报一行"还有多少项", 让模型自己决定往哪儿走.

target_declaration_ability=expandable: 扫描用的是 ExecutionContext 里已经冻结的那份
文件系统视图, 目标集合在 prepare 阶段就封闭 (ADR-0004 §4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import (
    IGNORED_SEGMENTS,
    emit_text,
    joined,
    read_capability,
    resolve_target,
    validate_arguments,
)
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathFacts, PathKind
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

__all__ = ["ScanTreeTool"]

_DEFAULT_DEPTH = 3
_DEFAULT_BUDGET = 600
_MAX_BUDGET = 4000

_SPEC = ToolSpec(
    name="fs.scan_tree",
    version="1",
    title="扫描目录结构",
    description=(
        "按层级列出一个目录的结构, 用来快速认识不熟悉的代码库. "
        "默认从工作区根开始, 展开 3 层; 深度到头的目录只报还有多少项, 不再展开. "
        "要看更深传 depth. "
        "默认跳过 .git, node_modules, target 一类生成目录, 需要它们时传 "
        "include_ignored=true. "
        "已经知道文件名或后缀时用 fs.list_files 更直接."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "depth": {"type": "integer", "minimum": 1, "maximum": 12},
            "include_ignored": {"type": "boolean"},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": _MAX_BUDGET},
        },
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"tree": {"type": "string"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_READ, Capability.EXTERNAL_READ}
    ),
    target_declaration_ability=TargetDeclarationAbility.EXPANDABLE,
    default_timeout_seconds=20.0,
    artifact_policy=ArtifactPolicy(max_inline_bytes=32 * 1024),
)


@dataclass
class _Scan:
    """一次扫描的累积状态.

    remaining 是**条目预算**而不是行数上限: 超预算就停在半路并如实说明, 而不是悄悄
    截断. 模型看到"已达上限"才会知道该缩小 path 再来一次, 否则它会把这份残缺的树当成
    完整结构去推断.
    """

    remaining: int
    lines: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    truncated: bool = False


class ScanTreeTool(Tool):
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
        _, facts = target
        if facts.kind is not PathKind.DIRECTORY:
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"不是目录: {facts.realpath}. 读单个文件请用 fs.read_file.",
                field_path="path",
            )

        depth = _int_or(request.arguments.get("depth"), _DEFAULT_DEPTH)
        include_ignored = bool(request.arguments.get("include_ignored", False))
        budget = _int_or(request.arguments.get("max_entries"), _DEFAULT_BUDGET)
        scan = _Scan(remaining=budget)
        _walk(
            context,
            facts.realpath,
            prefix="",
            depth_left=depth,
            include_ignored=include_ignored,
            scan=scan,
        )

        paths = tuple(scan.paths)
        scope = context.scope_of_all((facts.realpath, *paths))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "path": facts.realpath,
                    "depth": depth,
                    "include_ignored": include_ignored,
                    "max_entries": budget,
                    # 树在 prepare 就渲染好: 展开依据的是这次冻结的视图, 执行时再走一遍
                    # 目录, 拿到的可能已经不是裁决时看过的那棵树.
                    "lines": scan.lines,
                    "truncated": scan.truncated,
                    "entry_count": len(paths),
                }
            ),
            capabilities=frozenset({read_capability(scope)}),
            effects=PlanEffects(read_paths=paths or (facts.realpath,)),
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
        root = str(plan.normalized_input["path"])
        depth = int(str(plan.normalized_input["depth"]))
        raw_lines = plan.normalized_input.get("lines", [])
        lines = [str(item) for item in raw_lines] if isinstance(raw_lines, list) else []
        header = f"{root} (深度 {depth}, {len(lines)} 项)"
        body = [header, *lines] if lines else [header, _empty_message(root)]
        if plan.normalized_input.get("truncated"):
            body.insert(
                1,
                f"[已达 {plan.normalized_input['max_entries']} 项上限, 结构不完整; "
                "请对更小的 path 再扫一次]",
            )
        limits = self._governor.limits_for(_SPEC)
        emitted = emit_text(
            joined(body),
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="scan_tree",
        )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=emitted.parts,
            artifacts=emitted.artifacts,
            metrics=ToolMetrics(bytes_out=emitted.bytes_out),
        )


def _empty_message(root: str) -> str:
    return (
        f"{root} 是空目录, 或者其中只有被默认忽略的生成目录 "
        "(.git, node_modules, target 一类); 需要它们请传 include_ignored=true."
    )


def _int_or(raw: object, fallback: int) -> int:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else fallback


def _walk(
    context: ExecutionContext,
    directory: str,
    *,
    prefix: str,
    depth_left: int,
    include_ignored: bool,
    scan: _Scan,
) -> None:
    """深度优先渲染一层, 目录在前, 同类按名字排序.

    排序是刻意的: 同一个目录扫两次要给出同一棵树, 否则 plan_hash 会随目录项的返回顺序
    变化, 同一次批准也就绑不住第二次调用.
    """
    for name, facts in _children(context, directory, include_ignored=include_ignored):
        if scan.remaining <= 0:
            scan.truncated = True
            return
        scan.remaining -= 1
        scan.paths.append(facts.realpath or f"{directory}/{name}")
        if facts.kind is not PathKind.DIRECTORY:
            scan.lines.append(f"{prefix}{name}{_size_of(facts)}")
            continue
        if depth_left <= 1:
            # 深度到头. 报直接子项数而不是静默停住 —— "空目录"和"没展开"长得完全不同,
            # 混起来模型会以为这条路走到头了.
            held = len(
                _children(context, facts.realpath, include_ignored=include_ignored)
            )
            suffix = f"  ({held} 项未展开)" if held else "  (空)"
            scan.lines.append(f"{prefix}{name}/{suffix}")
            continue
        scan.lines.append(f"{prefix}{name}/")
        _walk(
            context,
            facts.realpath,
            prefix=f"{prefix}  ",
            depth_left=depth_left - 1,
            include_ignored=include_ignored,
            scan=scan,
        )


def _children(
    context: ExecutionContext, directory: str, *, include_ignored: bool
) -> tuple[tuple[str, PathFacts], ...]:
    found: list[tuple[str, PathFacts]] = []
    for name in context.filesystem.list_dir(directory):
        if not include_ignored and name in IGNORED_SEGMENTS:
            continue
        facts = context.filesystem.facts(f"{directory.rstrip('/')}/{name}")
        if not facts.exists:
            continue
        found.append((name, facts))
    found.sort(key=lambda item: (item[1].kind is not PathKind.DIRECTORY, item[0]))
    return tuple(found)


def _size_of(facts: PathFacts) -> str:
    """文件大小影响"要不要读它", 所以进树; 目录不报, 那只是目录项本身的字节数."""
    size = facts.size
    if size <= 0:
        return ""
    if size < 1024:
        return f"  {size}B"
    if size < 1024 * 1024:
        return f"  {size / 1024:.1f}K"
    return f"  {size / (1024 * 1024):.1f}M"
