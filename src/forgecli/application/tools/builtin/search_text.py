"""search_text: 在工作区文件里找子串 (ADR-0004 §14).

不 shell out 到 grep/rg: 那会把一次纯读取变成 EXECUTE_SHELL, 让搜索这种最常用的动作
每次都去撞 Shell 分析路径. 纯 Python 扫描慢一点, 但能力窄, 目标集合可封闭, 在 plan
档也能用.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

import regex

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import (
    emit_text,
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
    ToolError,
    ToolMetrics,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolAction,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["SearchTextTool"]

_MAX_FILES = 2000
_MAX_MATCHES = 200
_MAX_FILE_BYTES = 1024 * 1024
_MAX_REGEX_LINE_CHARS = 16 * 1024
# 单行正则匹配的墙钟上限. 取代原先按语法拒绝正则的那套判据 —— 要防的是跑不完,
# 不是括号.
_REGEX_LINE_TIMEOUT = 0.25
_MAX_GLOB_CANDIDATES = 10_000

_SPEC = ToolSpec(
    name="search_text",
    version="5",
    title="搜索文本",
    description=(
        "在**文件内容**里搜索一段文字, 按文件分组返回 行号:内容. "
        "它命中的是文字出现的每一处, 定义, import, 注释与调用点都算.\n"
        "query 是要在内容里查的文字. "
        "path 可以是目录, 也可以是单个文件. "
        "默认递归扫描 path 下的整棵树 (in_files 默认 '**/*'), 不需要先列目录. "
        "in_files 是文件名 glob, 只用来把扫描范围缩窄, 例如 in_files='**/*.java'; "
        "它不参与内容匹配. "
        "默认按子串匹配 (区分大小写), 传 regex=true 时 query 作为正则, "
        "此时可以写 (?i) 前缀做大小写不敏感搜索. "
        "context_lines 给出每个命中的前后文行数. "
        "默认跳过 .git, node_modules, target 一类生成目录, "
        "需要它们时传 include_ignored=true. "
        "结果按源码优先排序, 日志与构建输出靠后."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": 512,
                "description": (
                    "要在文件内容里查找的文字. regex=false 时按字面子串匹配, "
                    "区分大小写."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "搜索根目录, 也可以是单个文件; 省略时从工作区根递归搜索."
                ),
            },
            "in_files": {
                "type": "string",
                "maxLength": 1024,
                "description": (
                    "把扫描范围缩窄到哪些文件的 glob, 例如 '**/*.java'. "
                    "它筛的是**文件路径**, 不参与内容匹配 —— "
                    "要搜的文字写在 query 里."
                ),
            },
            "regex": {
                "type": "boolean",
                "description": (
                    "为 true 时把 query 当正则. 单行匹配有超时保护, 语法不设限, "
                    "(?i) 与转义括号都可以用."
                ),
            },
            "context_lines": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": "每个命中行前后附带的上下文行数.",
            },
            "include_ignored": {
                "type": "boolean",
                "description": (
                    "为 true 时不跳过 .git, node_modules, target 一类生成目录, "
                    "也不套用工作区 .gitignore."
                ),
            },
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
    action=ToolAction.LOCATE_TEXT,
)


# 靠后的文件类型: 命中它们几乎总是噪音, 而它们的数量常常比源码多一个数量级.
# ADR-0040 B 类表: 只影响**排序**, 不排除任何结果 (ADR-0040 §8.5).
# 表外默认: 没被点名的文件按正常顺序参与检索.
# 漏一项的后果: 噪音排得靠前一点, 不会让一个文件从结果里消失.
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
# ADR-0040 B 类表: 同样只影响排序. 忽略是用户数据 (走 git), 低价值排序是启发式,
# 两者必须分开 —— 一个叫 vendor 的目录不该在默认检索里消失.
# 表外默认: 没被点名的目录段按正常顺序参与检索.
# 漏一项的后果: 噪音排得靠前一点.
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
    """一行是否命中. 正则一律带超时, 语法上不设限.

    原先这里挡的是**语法**: 含 `( ) { } |` 的正则一律拒掉, 理由是 Python `re` 没有可
    中止的匹配超时, `(a|aa)+$` 这类表达式会卡住整个 Agent. 那条判据用裸子串扫, 认不出
    转义 —— 于是 `def \\w+\\(` 这种最常见的代码检索正则也被拒了, 而 `\\(` 只是一个字面
    左括号, 与回溯毫无关系. `(?i)` 同样被误伤, 这是 search_text 至今做不了大小写不敏感
    搜索的直接原因.

    `regex` 模块的 `timeout=` 直接解决了原来的动机: 实测 `(a|aa)+$` 在 0.5 秒准时抛
    TimeoutError. 判据从"这个正则长什么样"换成"它跑了多久", 后者才是真正要防的东西.
    """
    if not use_regex:
        return lambda line: query in line
    compiled = regex.compile(query)

    def hit(line: str) -> bool:
        # 超时按"这一行不匹配"处理而不是往上抛: 一次搜索扫几百个文件, 单行病态回溯不该
        # 让整次调用失败. 真正跑满超时的行会拖慢搜索, 但拖不垮它.
        try:
            return compiled.search(line, timeout=_REGEX_LINE_TIMEOUT) is not None
        except TimeoutError:
            return False

    return hit


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

    只回"未找到匹配"时, 模型分不清是"确实没有"还是"搜错地方了", 于是反复换 in_files
    重试. 给出扫描文件数就能让它自己判断 —— 扫了 0 个文件说明 in_files 不对, 扫了 300 个
    说明这个词确实不在代码里.
    """
    query = plan.normalized_input["query"]
    root = plan.normalized_input["path"]
    in_files = plan.normalized_input["in_files"]
    if scanned == 0:
        return (
            f"没有文件被扫描: {root} 下没有匹配 {in_files} 的文件. "
            "问题出在 in_files 或 path 上, 不是搜索词. "
            "注意 .git, node_modules, target 一类生成目录默认被跳过, "
            "需要它们时传 include_ignored=true."
        )
    conclusion = (
        "这是确定的空结果."
        if complete
        else "扫描受资源上限影响，结果不完整，不能据此断言全文不存在."
    )
    return (
        f"在 {root} 下扫描了 {scanned} 个文件 (in_files={in_files}), "
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
                regex.compile(query)
            except regex.error as exc:
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
        in_files = str(request.arguments.get("in_files", "**/*"))
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
                in_files=in_files,
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
            in_files,
            root=facts.realpath,
            max_results=_MAX_GLOB_CANDIDATES + 1,
            skip_ignored=not include_ignored,
        )
        expansion_truncated = len(raw_candidates) > _MAX_GLOB_CANDIDATES
        candidates = raw_candidates[:_MAX_GLOB_CANDIDATES]
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
            in_files=in_files,
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
        in_files: str,
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
                    "in_files": in_files,
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
        skipped_binary = 0
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
                    error=ToolError(
                        code="target_changed", message=message, retryable=True
                    ),
                )
            text = context.filesystem.read_text_if_text(path, max_bytes=_MAX_FILE_BYTES)
            if text is None:
                # 二进制文件. 跳过而不是解码后硬扫: 替换字符也能"命中", 而命中额度被
                # 噪音吃光之后, "这个词不在代码里"这个结论建立在没扫到源码上.
                skipped_binary += 1
                continue
            lines = text.splitlines()
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
        if skipped_binary:
            incomplete_notes.append(f"跳过了 {skipped_binary} 个二进制文件")
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
