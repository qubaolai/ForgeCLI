"""search.text: 在工作区文件里找子串 (ADR-0004 §14).

不 shell out 到 grep/rg: 那会把一次纯读取变成 EXECUTE_SHELL, 让搜索这种最常用的动作
每次都去撞 Shell 分析路径. 纯 Python 扫描慢一点, 但能力窄, 目标集合可封闭, 在 plan
档也能用.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from types import MappingProxyType

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import (
    emit_text,
    joined,
    read_capability,
    resolve_target,
    validate_arguments,
)
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
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

__all__ = ["SearchTextTool"]

_MAX_FILES = 2000
_MAX_MATCHES = 200
_MAX_FILE_BYTES = 1024 * 1024

_SPEC = ToolSpec(
    name="search.text",
    version="2",
    title="搜索文本",
    description=(
        "在工作区文件中搜索, 按文件分组返回 行号:内容. "
        "默认按子串匹配, 传 regex=true 时 query 作为 Python 正则. "
        "context_lines 给出每个命中的前后文行数."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "pattern": {"type": "string"},
            "regex": {"type": "boolean"},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 10},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"matches": {"type": "array"}}},
    declared_capabilities=frozenset({Capability.WORKSPACE_READ}),
    target_declaration_ability=TargetDeclarationAbility.EXPANDABLE,
    default_timeout_seconds=30.0,
)


def _context_lines(arguments: Mapping[str, object]) -> int:
    raw = arguments.get("context_lines", 0)
    return max(0, min(10, raw)) if isinstance(raw, int) else 0


def _matcher(query: str, use_regex: bool) -> Callable[[str], bool]:
    if not use_regex:
        return lambda line: query in line
    compiled = re.compile(query)
    return lambda line: compiled.search(line) is not None


def _render_hits(lines: list[str], numbers: list[int], around: int) -> list[str]:
    """输出命中行及其前后文. 相邻命中的上下文合并, 不重复打同一行."""
    wanted: set[int] = set()
    for number in numbers:
        low = max(1, number - around)
        high = min(len(lines), number + around)
        wanted.update(range(low, high + 1))
    rendered: list[str] = []
    previous = 0
    for number in sorted(wanted):
        if previous and number > previous + 1:
            rendered.append("  --")
        marker = ":" if number in set(numbers) else "-"
        rendered.append(f"  {number}{marker}{lines[number - 1].rstrip()}")
        previous = number
    return rendered


def _empty_message(plan: ToolPlan, scanned: int) -> str:
    """把"没找到"说清楚: 搜了几个文件, 搜的是什么, 在哪儿搜的.

    只回"未找到匹配"时, 模型分不清是"确实没有"还是"搜错地方了", 于是反复换 pattern
    重试. 给出扫描文件数就能让它自己判断 —— 扫了 0 个文件说明 pattern 不对, 扫了 300 个
    说明这个词确实不在代码里.
    """
    query = plan.normalized_input["query"]
    root = plan.normalized_input["path"]
    pattern = plan.normalized_input["pattern"]
    if scanned == 0:
        return (
            f"没有文件被扫描: {root} 下没有匹配 {pattern} 的文件. "
            "问题出在 pattern 或 path 上, 不是搜索词."
        )
    return (
        f"在 {root} 下扫描了 {scanned} 个文件 (pattern={pattern}), "
        f"没有一行包含 {query!r}. 这是确定的空结果."
    )


class SearchTextTool(Tool):
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
        query = str(request.arguments.get("query", ""))
        if not query:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="query 不能为空",
                field_path="query",
            )
        use_regex = bool(request.arguments.get("regex", False))
        if use_regex:
            try:
                re.compile(query)
            except re.error as exc:
                # 非法正则在 prepare 就挡下: 让它进执行阶段只会变成一次没有结果的调用,
                # 模型读不出"是我正则写错了".
                return PreparationError(
                    code=PreparationErrorCode.INVALID_INPUT,
                    message=f"query 不是合法正则: {exc}",
                    field_path="query",
                )
        target = resolve_target(
            request.arguments.get("path", context.primary_root), context
        )
        if isinstance(target, PreparationError):
            return target
        _, facts = target
        pattern = str(request.arguments.get("pattern", "**/*"))
        files = context.filesystem.expand_glob(pattern, root=facts.realpath)[
            :_MAX_FILES
        ]
        scope = context.scope_of_all((facts.realpath, *files))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "query": query,
                    "path": facts.realpath,
                    "pattern": pattern,
                    "regex": use_regex,
                    "context_lines": _context_lines(request.arguments),
                    "files": list(files),
                }
            ),
            capabilities=frozenset({read_capability(scope)}),
            effects=PlanEffects(read_paths=files or (facts.realpath,)),
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
        query = str(plan.normalized_input["query"])
        raw_files = plan.normalized_input.get("files", [])
        files = [str(item) for item in raw_files] if isinstance(raw_files, list) else []
        raw_around = plan.normalized_input.get("context_lines", 0)
        around = raw_around if isinstance(raw_around, int) else 0
        hit = _matcher(query, bool(plan.normalized_input.get("regex", False)))
        matches: list[str] = []
        total = 0
        for path in files:
            if cancel is not None and cancel.cancelled or total >= _MAX_MATCHES:
                break
            lines = context.filesystem.read_text(
                path, max_bytes=_MAX_FILE_BYTES
            ).splitlines()
            numbers = [index for index, line in enumerate(lines, start=1) if hit(line)][
                : _MAX_MATCHES - total
            ]
            if not numbers:
                continue
            total += len(numbers)
            # 按文件分组: 每个路径只打一次, 省下的是上下文里最不值钱的重复.
            matches.append(f"{path}  ({len(numbers)} 处)")
            matches.extend(_render_hits(lines, numbers, around))
        limits = self._governor.limits_for(_SPEC)
        parts, artifacts = emit_text(
            joined(matches) or _empty_message(plan, len(files)),
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="search_text",
        )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=parts,
            artifacts=artifacts,
        )
