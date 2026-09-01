"""code_definitions: 按符号名找**定义**, 用语法树而不是文本匹配.

## 为什么要它

`search_text` 是逐行子串匹配, 搜 `UsageMeter` 会同时命中定义, import, 类型标注, 注释和
字符串. 真实会话里模型的应对是自己造一个近似的符号检索: 29 次 search_text 里有 6 次
写的是 `class UsageMeter`, `class UsageRecordDraft`, `def build_llm_runtime`,
`class .*Outcome` —— 它在用文本匹配模拟"找定义", 而这个模拟只在"定义和名字写在同一行且
中间恰好一个空格"时成立. 换成 Java 的 `public final class X`, Go 的 `func (r *R) X()`,
TypeScript 的 `export const X = () => {}` 就全落空.

提示词里那条"读到定义之后, 搜的应该是它的**引用**"同样悬着: 引用要能和定义分开, 前提是
有一个工具能单独给出定义, 而在此之前没有.

## 为什么是一个新工具而不是 search_text 的一个参数

ADR-0029 的判据是"加参数会让每次调用都要读它, 加工具要占掉工具数的额度". 这里选后者,
因为**问的问题不同**: search_text 问"这段文字出现在哪", 这里问"这个符号定义在哪". 做成
`search_text(mode='symbol')` 的话, 模型每次做普通文本搜索都要先排除一个它用不到的模式.

也因此这个工具刻意只有一个意图, 不做"列出这个文件里所有符号"的第二模式 —— fs_find 的
双模态 (传不传 pattern 决定是树还是清单) 已经证明隐式模式会让模型选错.

## 边界

- **只找定义, 不找引用.** description 第一句就把这条说给模型听, 并指回 search_text.
- **认不出语言的文件直接跳过.** `detect_language_from_path` 认不出的多半是数据文件与二
  进制, 跳过它们同时省掉了单独判二进制这一步.
- **解析超时按文件计.** `parse_timeout_ms` 是 language-pack 自带的; 一个畸形或超大的
  源文件不该拖垮整次调用, 它只该让自己那一行变成"没解析出来".
"""

from __future__ import annotations

from types import MappingProxyType

from tree_sitter_language_pack import (
    ProcessConfig,
    detect_language_from_path,
    process,
)

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
from forgecli.domain.tool.result import ToolMetrics, ToolResult, ToolResultStatus
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolAction,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["CodeDefinitionsTool"]

_MAX_FILES = 2000
_MAX_HITS = 200
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_GLOB_CANDIDATES = 10_000
# 单文件解析的墙钟上限. 与 search_text 的正则超时同一个道理: 拦的是"跑不完", 不是
# "长什么样".
_PARSE_TIMEOUT_MS = 500

_SPEC = ToolSpec(
    name="code_definitions",
    version="1",
    title="按符号名找定义",
    description=(
        "找一个类, 函数, 方法, 接口或常量**定义在哪一行**, 按语法树判断, "
        "不会命中 import, 注释, 字符串或调用点. "
        "symbol 是符号名, 默认精确匹配 (区分大小写); "
        "传 prefix=true 时按前缀匹配, 用来一次看完一族同名开头的符号. "
        "path 可以是目录或单个文件, 省略时从工作区根开始. "
        "kind 可以把结果收窄到某一类, 取值见枚举. "
        "认不出语言的文件会被跳过, 跳过的数量会在结果里说明."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "minLength": 1, "maxLength": 256},
            "path": {"type": "string"},
            "prefix": {"type": "boolean"},
            "kind": {
                "type": "string",
                "enum": [
                    "class",
                    "function",
                    "interface",
                    "enum",
                    "constant",
                    "variable",
                    "type",
                    "module",
                ],
            },
        },
        "required": ["symbol"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"definitions": {"type": "array"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_READ, Capability.EXTERNAL_READ}
    ),
    target_declaration_ability=TargetDeclarationAbility.EXPANDABLE,
    default_timeout_seconds=60.0,
    action=ToolAction.LOCATE_SYMBOL,
)


class CodeDefinitionsTool(Tool):
    def __init__(
        self, governor: ResourceGovernor, artifacts: ArtifactStore | None = None
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
        symbol = str(request.arguments.get("symbol", "")).strip()
        if not symbol:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="symbol 不能为空",
                field_path="symbol",
            )
        target = resolve_target(
            request.arguments.get("path", context.primary_root), context
        )
        if isinstance(target, PreparationError):
            return target
        _, facts = target
        if facts.is_symlink:
            return PreparationError(
                code=PreparationErrorCode.UNSUPPORTED_REQUEST,
                message=f"code_definitions 不跟随符号链接: {facts.realpath}",
                field_path="path",
            )
        files: tuple[str, ...]
        if facts.kind is PathKind.FILE:
            files, skipped, truncated = (facts.realpath,), 0, False
        elif facts.kind is PathKind.DIRECTORY:
            files, skipped, truncated = self._candidates(context, facts.realpath)
        else:
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"code_definitions 的 path 必须是文件或目录: {facts.realpath}",
                field_path="path",
            )
        scope = context.scope_of_all((facts.realpath, *files))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "symbol": symbol,
                    "path": facts.realpath,
                    "prefix": bool(request.arguments.get("prefix", False)),
                    "kind": str(request.arguments.get("kind", "")),
                    "files": list(files),
                    "file_states": tuple(
                        (path, path_state_token(context.filesystem.facts(path)))
                        for path in files
                    ),
                    "skipped_unsupported": skipped,
                    "files_truncated": truncated,
                }
            ),
            capabilities=frozenset({read_capability(scope)}),
            effects=PlanEffects(read_paths=files or (facts.realpath,)),
            # 目标集合已由 Forge 用冻结视图展开并封闭.
            target_resolution=TargetResolution.FORGE_EXPANDED,
            workspace_scope=scope,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def _candidates(
        self, context: ExecutionContext, root: str
    ) -> tuple[tuple[str, ...], int, bool]:
        """目录下能解析的源文件. 认不出语言的当场丢掉, 不进目标集合.

        丢在 prepare 而不是 perform: 目标集合要在裁决之前就封闭, 一堆我们压根不打算解析
        的文件不该出现在 read_paths 里让安全侧为它们做判断.
        """
        raw = context.filesystem.expand_glob(
            "**/*",
            root=root,
            max_results=_MAX_GLOB_CANDIDATES + 1,
            skip_ignored=True,
        )
        supported: list[str] = []
        skipped = 0
        for path in raw[:_MAX_GLOB_CANDIDATES]:
            candidate = context.filesystem.facts(path)
            if candidate.kind is not PathKind.FILE or candidate.is_symlink:
                continue
            if _language_of(path) is None:
                skipped += 1
                continue
            supported.append(candidate.realpath)
        supported.sort()
        return tuple(supported[:_MAX_FILES]), skipped, len(supported) > _MAX_FILES

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        symbol = str(plan.normalized_input["symbol"])
        by_prefix = bool(plan.normalized_input.get("prefix", False))
        wanted_kind = str(plan.normalized_input.get("kind", ""))
        raw_files = plan.normalized_input.get("files", [])
        files = [str(item) for item in raw_files] if isinstance(raw_files, list) else []
        expected = _state_map(plan.normalized_input.get("file_states", ()))

        lines: list[str] = []
        hits = 0
        unparsed = 0
        cancelled = False
        for path in files:
            if cancel is not None and cancel.cancelled:
                cancelled = True
                break
            if hits >= _MAX_HITS:
                break
            if path_state_token(context.filesystem.facts(path)) != str(
                expected.get(path, "")
            ):
                return _stale(plan, path)
            source = context.filesystem.read_text_if_text(
                path, max_bytes=_MAX_FILE_BYTES
            )
            if source is None:
                continue
            found = _definitions_in(source, path)
            if found is None:
                unparsed += 1
                continue
            matched = [
                item
                for item in found
                if _matches(item, symbol, by_prefix=by_prefix, kind=wanted_kind)
            ]
            if not matched:
                continue
            hits += len(matched)
            lines.append(path)
            lines.extend(f"  {item.line}: {item.kind} {item.name}" for item in matched)

        notes: list[str] = []
        if plan.normalized_input.get("files_truncated"):
            notes.append(f"源文件超过 {_MAX_FILES} 个, 只解析了前 {_MAX_FILES} 个")
        if unparsed:
            notes.append(f"{unparsed} 个文件解析超时或失败")
        if hits >= _MAX_HITS:
            notes.append(f"命中达到 {_MAX_HITS} 条上限")
        if cancelled:
            notes.append("查找被取消")
        body = joined(lines) or _empty_message(plan, len(files), complete=not notes)
        if notes:
            body = "[结果不完整: " + "; ".join(notes) + "]\n" + body
        emitted = emit_text(
            body,
            invocation_id=plan.plan_id,
            limits=self._governor.limits_for(_SPEC),
            artifacts=self._artifacts,
            artifact_name="code_definitions",
        )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=(ToolResultStatus.CANCELLED if cancelled else ToolResultStatus.OK),
            content_parts=emitted.parts,
            artifacts=emitted.artifacts,
            metrics=ToolMetrics(bytes_out=emitted.bytes_out),
            provenance=emitted.provenance,
        )


class _Definition:
    """一条定义. 用普通类而不是 dataclass: 只在本模块内传递, 不进任何持久结构."""

    __slots__ = ("kind", "line", "name")

    def __init__(self, name: str, kind: str, line: int) -> None:
        self.name = name
        self.kind = kind
        self.line = line


def _language_of(path: str) -> str | None:
    """按扩展名判语言. 认不出就是 None —— 数据文件与二进制都落在这里."""
    try:
        return detect_language_from_path(path)
    except Exception:
        # language-pack 对没见过的扩展名抛的异常类型不在公开契约里, 而"认不出"
        # 本来就是正常路径, 不该让一次调用失败.
        return None


def _definitions_in(source: str, path: str) -> tuple[_Definition, ...] | None:
    """一个文件里的全部定义. 解析不了返回 None —— 与"解析出来但没有定义"要分得开."""
    language = _language_of(path)
    if language is None:
        return None
    try:
        result = process(
            source,
            ProcessConfig(
                language=language,
                symbols=True,
                structure=False,
                imports=False,
                exports=False,
                parse_timeout_ms=_PARSE_TIMEOUT_MS,
                max_source_bytes=_MAX_FILE_BYTES,
            ),
        )
    except Exception:
        return None
    return tuple(
        # start_line 从 0 起, 而模型看到的行号必须和 fs_read / search_text 一致,
        # 都从 1 起.
        _Definition(item.name, str(item.kind).lower(), item.span.start_line + 1)
        for item in result.symbols
    )


def _matches(item: _Definition, symbol: str, *, by_prefix: bool, kind: str) -> bool:
    if kind and item.kind != kind:
        return False
    return item.name.startswith(symbol) if by_prefix else item.name == symbol


def _empty_message(plan: ToolPlan, scanned: int, *, complete: bool) -> str:
    """把"没找到"说清楚: 解析了几个文件, 找的是什么, 在哪儿找的.

    与 search_text 的空结果同一个道理: 只回"未找到"时, 模型分不清"确实没有定义"和
    "找错地方了", 于是换个写法反复重试. 这里还要多说一件事 —— 定义找不到不代表这个符号
    不存在, 它可能来自依赖库, 而那不在工作区里.
    """
    symbol = plan.normalized_input["symbol"]
    root = plan.normalized_input["path"]
    if scanned == 0:
        return (
            f"没有文件被解析: {root} 下没有可识别语言的源文件. "
            "问题出在 path 上, 不是 symbol."
        )
    tail = (
        "这是确定的空结果."
        if complete
        else "解析未覆盖全部文件, 不能据此断言工作区里没有它."
    )
    return (
        f"在 {root} 下解析了 {scanned} 个源文件, 没有名为 {symbol!r} 的定义. "
        f"它可能来自依赖库而不是这个工作区; 要找它被用在哪里请用 search_text. {tail}"
    )


def _stale(plan: ToolPlan, path: str) -> ToolResult:
    from forgecli.domain.tool.result import ToolError

    return ToolResult(
        invocation_id=plan.plan_id,
        tool_name=_SPEC.name,
        status=ToolResultStatus.TOOL_ERROR,
        error=ToolError(
            code="target_changed",
            message=f"文件在计划生成后发生变化, 已拒绝继续解析: {path}",
            retryable=True,
        ),
    )


def _state_map(value: object) -> dict[str, str]:
    if not isinstance(value, tuple):
        return {}
    return {str(path): str(token) for path, token in value}
