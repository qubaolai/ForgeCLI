"""search_text: 在工作区文件里找子串 (ADR-0004 §14).

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
    filter_globbed,
    joined,
    path_state_token,
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
    ContentPart,
    ToolError,
    ToolMetrics,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["SearchTextTool"]

_MAX_FILES = 2000
_MAX_MATCHES = 200
_MAX_FILE_BYTES = 1024 * 1024
_MAX_REGEX_LINE_CHARS = 16 * 1024
_MAX_GLOB_CANDIDATES = 10_000

_SPEC = ToolSpec(
    name="search_text",
    version="4",
    title="搜索文本",
    description=(
        "在文件内容里搜索, 按文件分组返回 行号:内容. "
        "path 可以是目录, 也可以是单个文件. "
        "默认递归扫描 path 下的整棵树 (pattern 默认 '**/*'), 不需要先列目录. "
        "pattern 是文件名 glob, 用来把扫描范围缩窄, 例如 pattern='**/*.java'. "
        "默认按子串匹配, 传 regex=true 时 query 作为 Python 正则. "
        "context_lines 给出每个命中的前后文行数. "
        "默认跳过 .git, node_modules, target 一类生成目录, "
        "需要它们时传 include_ignored=true. "
        "结果按源码优先排序, 日志与构建输出靠后."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 512},
            "path": {"type": "string"},
            "pattern": {"type": "string", "maxLength": 1024},
            "regex": {"type": "boolean"},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 10},
            "include_ignored": {"type": "boolean"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"matches": {"type": "array"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_READ, Capability.EXTERNAL_READ}
    ),
    target_declaration_ability=TargetDeclarationAbility.EXPANDABLE,
    default_timeout_seconds=30.0,
)


# 靠后的文件类型: 命中它们几乎总是噪音, 而它们的数量常常比源码多一个数量级.
_LOW_VALUE_SUFFIXES = (
    ".log",
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
    ".snap",
    ".po",
    ".mo",
)
_LOW_VALUE_SEGMENTS = ("dist", "build", "target", "out", "vendor", "coverage")


def _rank(path: str) -> tuple[int, str]:
    """排序键: 0 是源码, 1 是低价值文件. 同档内按路径, 保证同一次调用结果稳定.

    稳定很要紧: 顺序一变 plan_hash 就变, 上一次批准也就绑不住这一次.
    """
    lowered = path.casefold()
    if any(lowered.endswith(suffix) for suffix in _LOW_VALUE_SUFFIXES):
        return (1, path)
    segments = lowered.replace("\\", "/").split("/")
    if any(segment in _LOW_VALUE_SEGMENTS for segment in segments):
        return (1, path)
    return (0, path)


def _context_lines(arguments: Mapping[str, object]) -> int:
    raw = arguments.get("context_lines", 0)
    return max(0, min(10, raw)) if isinstance(raw, int) else 0


def _matcher(query: str, use_regex: bool) -> Callable[[str], bool]:
    if not use_regex:
        return lambda line: query in line
    compiled = re.compile(query)
    return lambda line: compiled.search(line) is not None


def _unsafe_regex_reason(query: str) -> str | None:
    """只允许不会形成嵌套/分支回溯的正则子集。

    Python ``re`` 没有可中止的匹配超时；把任意模型正则放进主进程会让 `(a|aa)+$`
    这类表达式卡住整个 Agent。高级正则应走受超时约束的 shell/rg 路径。
    """
    if any(token in query for token in ("(", ")", "{", "}", "|")):
        return "分组、分支与花括号量词不在安全正则子集中"
    if re.search(r"\\[1-9]", query):
        return "反向引用不在安全正则子集中"
    if sum(query.count(token) for token in ("*", "+")) > 4:
        return "无界量词过多"
    return None


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


def _empty_message(plan: ToolPlan, scanned: int, *, complete: bool) -> str:
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
            "问题出在 pattern 或 path 上, 不是搜索词. "
            "注意 .git, node_modules, target 一类生成目录默认被跳过, "
            "需要它们时传 include_ignored=true."
        )
    conclusion = (
        "这是确定的空结果."
        if complete
        else "扫描受资源上限影响，结果不完整，不能据此断言全文不存在."
    )
    return (
        f"在 {root} 下扫描了 {scanned} 个文件 (pattern={pattern}), "
        f"没有一行包含 {query!r}. {conclusion}"
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
            unsafe = _unsafe_regex_reason(query)
            if unsafe is not None:
                return PreparationError(
                    code=PreparationErrorCode.UNSUPPORTED_REQUEST,
                    message=(
                        f"该正则可能造成不可中止的回溯: {unsafe}. "
                        "请改用更简单的正则或经审批的 shell_run/rg."
                    ),
                    field_path="query",
                )
        target = resolve_target(
            request.arguments.get("path", context.primary_root), context
        )
        if isinstance(target, PreparationError):
            return target
        _, facts = target
        pattern = str(request.arguments.get("pattern", "**/*"))
        include_ignored = bool(request.arguments.get("include_ignored", False))
        if facts.kind is PathKind.FILE:
            # 收一个文件就是 files = [它] (ADR-0029 A 类). 早先这里直接返回
            # target_unreadable, 而那条规则没有安全理由 —— 能力声明与扫一个目录完全
            # 一样. **这是删一条规则, 不是加一个参数.**
            if facts.is_symlink:
                return PreparationError(
                    code=PreparationErrorCode.UNSUPPORTED_REQUEST,
                    message=f"search_text 不跟随符号链接: {facts.realpath}",
                    field_path="path",
                )
            return self._plan_for(
                request,
                context,
                root=facts.realpath,
                pattern=pattern,
                include_ignored=include_ignored,
                files=(facts.realpath,),
                expansion_truncated=False,
                files_truncated=False,
                skipped_symlinks=0,
            )
        if facts.kind is not PathKind.DIRECTORY:
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"search_text 的 path 必须是文件或目录: {facts.realpath}",
                field_path="path",
            )
        # 先过滤再截断, 顺序不能换: 反过来的话 target/ 下的几千个 class 文件会先把
        # _MAX_FILES 的额度吃光, 于是"这个词不在代码里"这个结论建立在没扫到源码上.
        raw_candidates = context.filesystem.expand_glob(
            pattern,
            root=facts.realpath,
            max_results=_MAX_GLOB_CANDIDATES + 1,
        )
        expansion_truncated = len(raw_candidates) > _MAX_GLOB_CANDIDATES
        candidates = filter_globbed(
            raw_candidates[:_MAX_GLOB_CANDIDATES],
            root=facts.realpath,
            include_ignored=include_ignored,
        )
        regular: list[str] = []
        skipped_symlinks = 0
        for candidate in candidates:
            candidate_facts = context.filesystem.facts(candidate)
            if candidate_facts.is_symlink:
                skipped_symlinks += 1
                continue
            if candidate_facts.kind is PathKind.FILE:
                regular.append(candidate_facts.realpath)
        # 先排序再截断: 命中额度被 .log 与 .min.js 吃光时, "这个词不在代码里"这个结论
        # 建立在没扫到源码上. 零参数, 纯输出改进 (ADR-0029 A 类).
        ranked = sorted(regular, key=_rank)
        files_truncated = len(ranked) > _MAX_FILES
        files = tuple(ranked[:_MAX_FILES])
        return self._plan_for(
            request,
            context,
            root=facts.realpath,
            pattern=pattern,
            include_ignored=include_ignored,
            files=files,
            expansion_truncated=expansion_truncated,
            files_truncated=files_truncated,
            skipped_symlinks=skipped_symlinks,
        )

    def _plan_for(
        self,
        request: ToolInvocationRequest,
        context: ExecutionContext,
        *,
        root: str,
        pattern: str,
        include_ignored: bool,
        files: tuple[str, ...],
        expansion_truncated: bool,
        files_truncated: bool,
        skipped_symlinks: int,
    ) -> ToolPlan:
        query = str(request.arguments["query"])
        use_regex = bool(request.arguments.get("regex", False))
        large_files = sum(
            context.filesystem.facts(path).size > _MAX_FILE_BYTES for path in files
        )
        scope = context.scope_of_all((root, *files))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "query": query,
                    "path": root,
                    "pattern": pattern,
                    "regex": use_regex,
                    "context_lines": _context_lines(request.arguments),
                    "include_ignored": include_ignored,
                    "files": list(files),
                    "file_states": tuple(
                        (path, path_state_token(context.filesystem.facts(path)))
                        for path in files
                    ),
                    "files_truncated": files_truncated,
                    "large_files": large_files,
                    "skipped_symlinks": skipped_symlinks,
                    "expansion_truncated": expansion_truncated,
                }
            ),
            capabilities=frozenset({read_capability(scope)}),
            effects=PlanEffects(read_paths=files or (root,)),
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
        cancelled = False
        regex_line_truncated = False
        use_regex = bool(plan.normalized_input.get("regex", False))
        expected_states = _state_map(plan.normalized_input.get("file_states", ()))
        for path in files:
            if cancel is not None and cancel.cancelled:
                cancelled = True
                break
            if total >= _MAX_MATCHES:
                break
            if path_state_token(context.filesystem.facts(path)) != str(
                expected_states.get(path, "")
            ):
                message = f"文件在计划生成后发生变化，已拒绝继续搜索: {path}"
                return ToolResult(
                    invocation_id=plan.plan_id,
                    tool_name=_SPEC.name,
                    status=ToolResultStatus.TOOL_ERROR,
                    content_parts=(ContentPart(text=message),),
                    error=ToolError(
                        code="target_changed", message=message, retryable=True
                    ),
                )
            lines = context.filesystem.read_text(
                path, max_bytes=_MAX_FILE_BYTES
            ).splitlines()
            numbers: list[int] = []
            for index, line in enumerate(lines, start=1):
                candidate = line
                if use_regex and len(candidate) > _MAX_REGEX_LINE_CHARS:
                    candidate = candidate[:_MAX_REGEX_LINE_CHARS]
                    regex_line_truncated = True
                if hit(candidate):
                    numbers.append(index)
                    if len(numbers) >= _MAX_MATCHES - total:
                        break
            if not numbers:
                continue
            total += len(numbers)
            # 按文件分组: 每个路径只打一次, 省下的是上下文里最不值钱的重复.
            matches.append(f"{path}  ({len(numbers)} 处)")
            matches.extend(_render_hits(lines, numbers, around))
        limits = self._governor.limits_for(_SPEC)
        incomplete_notes: list[str] = []
        if plan.normalized_input.get("expansion_truncated"):
            incomplete_notes.append(
                f"glob 枚举超过 {_MAX_GLOB_CANDIDATES} 个候选，未继续展开"
            )
        if plan.normalized_input.get("files_truncated"):
            incomplete_notes.append(
                f"匹配文件超过 {_MAX_FILES} 个，仅扫描前 {_MAX_FILES} 个"
            )
        large_files = _int_value(plan.normalized_input.get("large_files", 0))
        if large_files:
            incomplete_notes.append(
                f"{large_files} 个文件超过 {_MAX_FILE_BYTES} 字节，仅扫描其前缀"
            )
        skipped_symlinks = _int_value(plan.normalized_input.get("skipped_symlinks", 0))
        if skipped_symlinks:
            incomplete_notes.append(f"跳过了 {skipped_symlinks} 个符号链接")
        if total >= _MAX_MATCHES:
            incomplete_notes.append(f"命中达到 {_MAX_MATCHES} 条上限")
        if regex_line_truncated:
            incomplete_notes.append("正则搜索跳过了超长行的后半段")
        if cancelled:
            incomplete_notes.append("搜索被取消")
        complete = not incomplete_notes
        body = joined(matches) or _empty_message(plan, len(files), complete=complete)
        if incomplete_notes:
            body = "[结果不完整：" + "；".join(incomplete_notes) + "]\n" + body
        emitted = emit_text(
            body,
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="search_text",
        )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=(ToolResultStatus.CANCELLED if cancelled else ToolResultStatus.OK),
            content_parts=emitted.parts,
            artifacts=emitted.artifacts,
            metrics=ToolMetrics(bytes_out=emitted.bytes_out),
            provenance=emitted.provenance,
            error=(
                ToolError(code="cancelled", message="搜索被取消") if cancelled else None
            ),
        )


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _state_map(value: object) -> dict[str, str]:
    if not isinstance(value, tuple):
        return {}
    result: dict[str, str] = {}
    for item in value:
        if isinstance(item, tuple) and len(item) == 2:
            result[str(item[0])] = str(item[1])
    return result
