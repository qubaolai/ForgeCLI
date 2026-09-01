"""fs_find: 定位文件与认识目录, 一个入口 (ADR-0029 A 类).

合并了原先的 `fs_scan_tree` 与 `fs_list_files`. 它们是**同一个动作的两个入口** ——
都在走目录, 差别只在渲染成树还是渲染成清单. 让模型在两个工具之间选一次, 换不来任何
东西, 只多一次选错的机会.

**输出形态跟着 name_glob 走, 不加参数** (ADR-0029 规则一: 工具可以变聪明, 不该变宽):

    不给 name_glob  ->  树视图. 你还不知道要找什么, 在认识这个目录.
    给了 name_glob  ->  扁平清单. 你知道要找什么, 在定位它.

参数原先叫 `pattern`, 与 `search_text.pattern` 撞了名字而意思相反: 那边的 pattern 是
"限定在哪些文件里找", 这边的是"我要找的东西". 日志里模型因此发出过
`fs_find(path="frontend", pattern="当前数据源")` —— 它想在内容里搜一段文字, 却填进了
文件名 glob. ADR-0029 把这类失效叫作"拿 A 工具的 schema 去填 B 工具的参数", 改名就是
让它填不进来.

这两件事本来就对应两种意图, 用一个已有参数区分比新加一个 `tree: bool` 诚实 ——
后者要求模型先想清楚"我要哪种输出", 而它真正知道的是"我要不要找特定的东西".
"""

from __future__ import annotations

from types import MappingProxyType

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import (
    emit_text,
    joined,
    limit_depth,
    read_capability,
    resolve_target,
    validate_arguments,
)
from forgecli.application.tools.builtin.tree_view import (
    DEFAULT_BUDGET,
    DEFAULT_DEPTH,
    MAX_BUDGET,
    Scan,
    empty_tree_message,
    int_or,
    walk,
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
    ArtifactPolicy,
    TargetDeclarationAbility,
    ToolAction,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["FindTool"]

_MAX_ENTRIES = 2000
_MAX_GLOB_CANDIDATES = 10_000

_SPEC = ToolSpec(
    name="fs_find",
    version="2",
    title="定位文件与认识目录",
    description=(
        "按**文件名**定位文件, 或者认识一个目录的结构. "
        "它只看路径与文件名, 不看文件内容.\n"
        "不传 name_glob 时按层级渲染目录树, 用来快速认识不熟悉的代码库; "
        "默认从工作区根开始展开 3 层, 深度到头的目录只报还有多少项.\n"
        "传了 name_glob 时按 glob 返回扁平文件清单, 相对给定目录展开: "
        "'*' 只匹配当前一层, '**/' 前缀递归整棵树 —— "
        "例如 name_glob='**/*.java' 列出所有 Java 文件, "
        "name_glob='**/application*.yml' 按文件名找配置.\n"
        "depth 限制层数, max_entries 限制条目数. "
        "默认跳过 .git, node_modules, target 一类生成目录, "
        "需要它们时传 include_ignored=true."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "name_glob": {
                "type": "string",
                "description": (
                    "文件名 glob, 例如 '**/*.java'. 它匹配的是**路径与文件名**, "
                    "不是文件内容; 传一段要搜的文字进来只会得到空结果."
                ),
            },
            "depth": {"type": "integer", "minimum": 1, "maximum": 32},
            "include_ignored": {"type": "boolean"},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": MAX_BUDGET},
        },
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"body": {"type": "string"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_READ, Capability.EXTERNAL_READ}
    ),
    target_declaration_ability=TargetDeclarationAbility.EXPANDABLE,
    default_timeout_seconds=20.0,
    action=ToolAction.LOCATE_PATH,
    artifact_policy=ArtifactPolicy(max_inline_bytes=32 * 1024),
)


class FindTool(Tool):
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
        target = resolve_target(
            request.arguments.get("path", context.primary_root), context
        )
        if isinstance(target, PreparationError):
            return target
        _, facts = target
        if facts.kind is not PathKind.DIRECTORY:
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"不是目录: {facts.realpath}. 读单个文件请用 fs_read.",
                field_path="path",
            )

        raw_glob = request.arguments.get("name_glob")
        include_ignored = bool(request.arguments.get("include_ignored", False))
        if isinstance(raw_glob, str) and raw_glob.strip():
            return self._locate(
                request, context, facts.realpath, raw_glob, include_ignored
            )
        return self._explore(request, context, facts.realpath, include_ignored)

    def _locate(
        self,
        request: ToolInvocationRequest,
        context: ExecutionContext,
        root: str,
        name_glob: str,
        include_ignored: bool,
    ) -> ToolPlan:
        raw_depth = request.arguments.get("depth")
        depth = raw_depth if isinstance(raw_depth, int) and raw_depth > 0 else None
        raw_matches = context.filesystem.expand_glob(
            name_glob,
            root=root,
            max_results=_MAX_GLOB_CANDIDATES + 1,
            skip_ignored=not include_ignored,
        )
        expansion_truncated = len(raw_matches) > _MAX_GLOB_CANDIDATES
        expanded = limit_depth(
            raw_matches[:_MAX_GLOB_CANDIDATES], root=root, depth=depth
        )
        limit = int_or(request.arguments.get("max_entries"), _MAX_ENTRIES)
        truncated = len(expanded) > limit
        matches = expanded[:limit]
        scope = context.scope_of_all((root, *matches))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "mode": "list",
                    "path": root,
                    "name_glob": name_glob,
                    "include_ignored": include_ignored,
                    "entries": list(matches),
                    "truncated": truncated,
                    "limit": limit,
                    "expansion_truncated": expansion_truncated,
                }
            ),
            capabilities=frozenset({read_capability(scope)}),
            effects=PlanEffects(read_paths=matches or (root,)),
            # 目标集合已由 Forge 用冻结视图展开并封闭.
            target_resolution=TargetResolution.FORGE_EXPANDED,
            workspace_scope=scope,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def _explore(
        self,
        request: ToolInvocationRequest,
        context: ExecutionContext,
        root: str,
        include_ignored: bool,
    ) -> ToolPlan:
        depth = int_or(request.arguments.get("depth"), DEFAULT_DEPTH)
        budget = int_or(request.arguments.get("max_entries"), DEFAULT_BUDGET)
        scan = Scan(remaining=budget)
        walk(
            context,
            root,
            prefix="",
            depth_left=depth,
            include_ignored=include_ignored,
            scan=scan,
        )
        paths = tuple(scan.paths)
        scope = context.scope_of_all((root, *paths))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "mode": "tree",
                    "path": root,
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
            effects=PlanEffects(read_paths=paths or (root,)),
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
        data = plan.normalized_input
        root = str(data["path"])
        limits = self._governor.limits_for(_SPEC)
        if str(data.get("mode")) == "tree":
            raw = data.get("lines", [])
            lines = [str(item) for item in raw] if isinstance(raw, list) else []
            # 头一行给根与规模: 树本身是相对路径, 不说根的话模型不知道这棵树长在哪.
            header = f"{root} (深度 {data['depth']}, {len(lines)} 项)"
            rendered = lines or [empty_tree_message(root)]
            body = joined([header, *rendered])
            notices = (
                [
                    f"已达 {data['max_entries']} 项上限, 结构不完整; "
                    "请对更小的 path 再扫一次"
                ]
                if data.get("truncated")
                else []
            )
        else:
            entries = data.get("entries", [])
            listed = (
                [str(item) for item in entries] if isinstance(entries, list) else []
            )
            # 空结果要说清"确实没有"而不是只回一句"(无匹配)": 后者与"参数写错了"长得
            # 一样, 模型只能换个写法再试一次, 而目录真空时换多少次都一样.
            body = joined(listed) or _empty_list_message(root, str(data["name_glob"]))
            notices = []
            if data.get("expansion_truncated"):
                notices.append(
                    f"glob 枚举超过 {_MAX_GLOB_CANDIDATES} 个候选, 未继续展开"
                )
            if data.get("truncated"):
                notices.append(
                    f"仅返回前 {data['limit']} 个匹配项; 请缩小 path 或 pattern"
                )
        if notices:
            body = "[结果不完整: " + "; ".join(notices) + "]\n" + body
        emitted = emit_text(
            body,
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="find",
        )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=emitted.parts,
            artifacts=emitted.artifacts,
            metrics=ToolMetrics(bytes_out=emitted.bytes_out),
            provenance=emitted.provenance,
        )


def _empty_list_message(root: str, name_glob: str) -> str:
    """空结果要能让模型分辨"确实没有"和"用错工具了".

    原先这句话以"目录存在且可读, 这是确定的空结果"收尾. 日志里模型发出
    `fs_find(path="frontend", name_glob="当前数据源")` —— 它想在内容里搜一段文字 ——
    然后读到这一句, 于是相信这个词在项目里不存在, 停止检索改去猜文件全文读.
    这句话把一个错误结论替它钉死了.

    判据是 glob 元字符: 一个既没有 `*?[]` 也没有 `/` 的 name_glob 只可能匹配一个同名
    文件, 而模型极少真的按完整文件名找东西. 这不是"猜它想干什么", 是指出这个取值本身
    几乎不可能是它要的.
    """
    if not _looks_like_glob(name_glob):
        return (
            f"目录 {root} 下没有名为 {name_glob!r} 的文件. "
            f"{name_glob!r} 里没有任何 glob 元字符 (* ? [ ]), 也不含路径分隔符, "
            "所以它只能匹配一个同名文件. "
            "如果你要找的是**文件内容**里的这段文字, 请用 search_text(query=...); "
            "如果要找的是一个类或函数的定义, 请用 code_definitions(symbol=...); "
            "按文件名找请写成 glob, 例如 '**/*.java'."
        )
    return (
        f"目录 {root} 下没有匹配 {name_glob} 的文件. "
        "已按默认规则忽略 .git, node_modules, __pycache__ 一类目录, "
        "以及工作区 .gitignore 里的规则; 需要它们请传 include_ignored=true. "
        "目录存在且可读, 这是确定的空结果."
    )


def _looks_like_glob(value: str) -> bool:
    return any(token in value for token in "*?[]/")
