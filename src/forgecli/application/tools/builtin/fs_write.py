"""fs.write_patch 与 fs.delete: 会真实改工作区的两个内置工具 (ADR-0004 §14).

写入顺序由 ADR-0015 §8.1 规定, 一步都不能提前:

    校验当前对象身份 -> 保存 preimage -> checkpoint ARMED -> 原子写入 -> 记录 postimage

工具本身**不建立**恢复事务 —— 那是协调器在签发授权之前做的事. 工具只在
`plan.normalized_input` 里拿到已经 ARMED 的事务句柄. 这样"没有恢复保障就不给授权"这条
规则由协调器统一保证, 而不是指望每个写工具自觉.

这两个工具用**结构化路径**参数, 所以能在 prepare 里给出封闭的目标集合. 这正是它们与
shell.run 的区别: 谁能证明目标集合, 谁就负责冻结它.

封闭程度两者不同, spec 的声明必须跟着 prepare 的实际产出走:
fs.write_patch 永远只碰一个路径, 所以是 STATIC; fs.delete 删目录时要把它展开成逐个文件,
产出 FORGE_EXPANDED, 所以必须声明 EXPANDABLE (ADR-0004 §4).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from forgecli.application.tools.builtin.base import validate_arguments
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathKind
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import (
    ContentPreview,
    DeclarationConfidence,
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

__all__ = ["DeleteTool", "WritePatchTool"]

_WRITE_SPEC = ToolSpec(
    name="fs.write_patch",
    version="2",
    title="精确替换文件片段",
    description=(
        "把文件里的 old_string 替换成 new_string. old_string 必须在文件中唯一出现, "
        "否则调用失败 —— 要替换多处请传 replace_all=true. "
        "old_string 传空串表示新建文件 (此时目标不能已存在). "
        "替换前会保存原内容以便撤销."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_WRITE, Capability.EXTERNAL_WRITE}
    ),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=15.0,
)

_DELETE_SPEC = ToolSpec(
    name="fs.delete",
    version="2",
    title="删除文件或目录",
    description=(
        "删除一个工作区文件或目录. 删除前会保存原内容以便撤销. "
        "删除非空目录必须显式传 recursive=true."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "recursive": {"type": "boolean"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_DELETE, Capability.EXTERNAL_WRITE}
    ),
    # EXPANDABLE 而不是 STATIC: 删目录时 prepare 会把它展开成逐个文件并给出
    # FORGE_EXPANDED, 而 STATIC 只允许 STATIC —— 声明成 STATIC 会让协调器的
    # _check_declaration 把每一次目录删除都 fail closed (ADR-0004 §14.5).
    target_declaration_ability=TargetDeclarationAbility.EXPANDABLE,
    default_timeout_seconds=15.0,
)


class WritePatchTool(Tool):
    """写文件. writer 由组合根注入, 便于测试与替换真实落盘实现."""

    def __init__(self, writer: Callable[[str, str], None]) -> None:
        self._write = writer

    @property
    def spec(self) -> ToolSpec:
        return _WRITE_SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(_WRITE_SPEC, request.arguments)
        if invalid is not None:
            return invalid
        raw = request.arguments.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="path 必须是非空字符串",
                field_path="path",
            )
        absolute = context.resolve(raw)
        facts = context.filesystem.facts(absolute)
        if facts.exists and facts.kind is not PathKind.FILE:
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"目标已存在且不是普通文件: {absolute}",
                field_path="path",
            )
        # 不存在的目标用字面绝对路径: realpath 对不存在的对象没有意义.
        target = facts.realpath if facts.exists else absolute
        content = _apply_replacement(request.arguments, target, facts.exists, context)
        if isinstance(content, PreparationError):
            return content
        scope = context.scope_for_write(target)
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_WRITE_SPEC.name,
            spec_hash=_WRITE_SPEC.spec_hash,
            # 替换在 prepare 阶段就算完, 计划里存的是**最终内容**. 这样裁决与审批绑定
            # 的是"文件会变成什么样", 而不是一段还要再解释一次的替换意图; 执行阶段也
            # 不必重读文件, 少一个 TOCTOU 窗口.
            normalized_input=MappingProxyType({"path": target, "content": content}),
            # 审批界面要逐字展示"文件会变成什么样". 内容本身已由 normalized_input 绑定,
            # 这里只是把它交出来 —— 工具层不能依赖安全模块, 所以要走一个中立结构.
            content_previews=(_preview(target, content),),
            capabilities=frozenset({_write_capability(scope)}),
            effects=PlanEffects(write_paths=(target,)),
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
        path = str(plan.normalized_input["path"])
        content = str(plan.normalized_input["content"])
        try:
            self._write(path, content)
        except OSError as exc:
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=_WRITE_SPEC.name,
                status=ToolResultStatus.TOOL_ERROR,
                error=ToolError(code="write_failed", message=str(exc)),
            )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_WRITE_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text=f"已写入 {path} ({len(content)} 字符)"),),
        )


class DeleteTool(Tool):
    def __init__(self, remover: Callable[[str], None]) -> None:
        self._remove = remover

    @property
    def spec(self) -> ToolSpec:
        return _DELETE_SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(_DELETE_SPEC, request.arguments)
        if invalid is not None:
            return invalid
        raw = request.arguments.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="path 必须是非空字符串",
                field_path="path",
            )
        absolute = context.resolve(raw)
        facts = context.filesystem.facts(absolute)
        if not facts.exists:
            return PreparationError(
                code=PreparationErrorCode.TARGET_NOT_FOUND,
                message=f"路径不存在: {absolute}",
                field_path="path",
            )
        if facts.kind is PathKind.OTHER:
            # 设备, FIFO, socket: 删它们的后果与删普通文件完全不同, 不在本工具范围内.
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"不是普通文件或目录, 拒绝删除: {facts.realpath}",
                field_path="path",
            )

        recursive = bool(request.arguments.get("recursive", False))
        if facts.kind is PathKind.DIRECTORY:
            targets = _files_under(context, facts.realpath)
            if targets and not recursive:
                # 目标集合封闭了才知道要删多少. 不给显式 recursive 就停在这里, 而不是
                # 让一个写错的路径把整棵树带走.
                return PreparationError(
                    code=PreparationErrorCode.INVALID_INPUT,
                    message=(
                        f"{facts.realpath} 是非空目录 ({len(targets)} 个文件), "
                        "删除它需要显式传 recursive=true"
                    ),
                    field_path="recursive",
                )
        else:
            targets = (facts.realpath,)

        # 目标集合是**逐个文件**而不是那一个目录路径: 恢复层按路径存 preimage, 审批
        # 界面按路径列清单. 只报一个目录名, 两边都不知道到底动了什么.
        scope = context.scope_for_write_all(targets or (facts.realpath,))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_DELETE_SPEC.name,
            spec_hash=_DELETE_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "path": facts.realpath,
                    "directory": facts.kind is PathKind.DIRECTORY,
                    "files": targets,
                }
            ),
            capabilities=frozenset({_delete_capability(scope)}),
            effects=PlanEffects(delete_paths=targets),
            # 目录展开由 Forge 完成且已封闭, 不是工具静态声明的.
            target_resolution=(
                TargetResolution.FORGE_EXPANDED
                if facts.kind is PathKind.DIRECTORY
                else TargetResolution.STATIC
            ),
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
        path = str(plan.normalized_input["path"])
        try:
            self._remove(path)
        except OSError as exc:
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=_DELETE_SPEC.name,
                status=ToolResultStatus.TOOL_ERROR,
                error=ToolError(code="delete_failed", message=str(exc)),
            )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_DELETE_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text=f"已删除 {path}"),),
        )


_MAX_SOURCE_BYTES = 4 * 1024 * 1024


def _apply_replacement(
    arguments: Mapping[str, object],
    target: str,
    exists: bool,
    context: ExecutionContext,
) -> str | PreparationError:
    """把 old_string -> new_string 算成最终文件内容.

    唯一性是硬约束而不是便利检查: 模型给的 old_string 命中多处时, 它想改的几乎总是其中
    一处. 默认替换全部会静默改掉另外几处, 默认替换第一处则取决于文件顺序 —— 两种都是
    "看起来成功了, 其实改错了". 所以命中多处直接失败, 让模型补足上下文或显式说明要全改.
    """
    old = str(arguments.get("old_string", ""))
    new = str(arguments.get("new_string", ""))
    replace_all = bool(arguments.get("replace_all", False))

    if not old:
        # 空 old_string = 新建. 不允许覆盖已存在的文件: 那是"全文覆盖"的后门, 而全文
        # 覆盖正是这次要去掉的东西.
        if exists:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=(
                    f"{target} 已存在. old_string 为空只用于新建文件; "
                    "要修改已有文件请给出待替换的片段."
                ),
                field_path="old_string",
            )
        return new

    if not exists:
        return PreparationError(
            code=PreparationErrorCode.TARGET_NOT_FOUND,
            message=f"文件不存在: {target}. 新建文件请把 old_string 留空.",
            field_path="path",
        )

    source = context.filesystem.read_text(target, max_bytes=_MAX_SOURCE_BYTES)
    occurrences = source.count(old)
    if occurrences == 0:
        return PreparationError(
            code=PreparationErrorCode.INVALID_INPUT,
            message=(
                f"在 {target} 中找不到 old_string. 它必须与文件内容逐字符一致, "
                "包括缩进与换行."
            ),
            field_path="old_string",
        )
    if occurrences > 1 and not replace_all:
        return PreparationError(
            code=PreparationErrorCode.INVALID_INPUT,
            message=(
                f"old_string 在 {target} 中出现了 {occurrences} 次. "
                "请扩大片段使其唯一, 或者传 replace_all=true 明确要全部替换."
            ),
            field_path="old_string",
        )
    return source.replace(old, new)


def _files_under(context: ExecutionContext, root: str) -> tuple[str, ...]:
    """递归列出目录下的全部文件 (不含目录本身).

    走 FileSystemView 而不是直接 os.walk: 展开必须基于这次调用冻结的那一份视图, 否则
    "审批时看到的清单"与"执行时真删的东西"可能不是一回事.
    """
    found: list[str] = []
    stack = [root]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            # 目录里有指回上层的符号链接时不至于转圈.
            continue
        seen.add(current)
        for name in context.filesystem.list_dir(current):
            child = f"{current}/{name}"
            facts = context.filesystem.facts(child)
            if facts.kind is PathKind.DIRECTORY:
                stack.append(facts.realpath or child)
            elif facts.exists:
                found.append(facts.realpath or child)
    return tuple(sorted(found))


def _write_capability(scope: WorkspaceScope) -> Capability:
    return (
        Capability.EXTERNAL_WRITE
        if scope is WorkspaceScope.OUTSIDE
        else Capability.WORKSPACE_WRITE
    )


def _delete_capability(scope: WorkspaceScope) -> Capability:
    return (
        Capability.EXTERNAL_WRITE
        if scope is WorkspaceScope.OUTSIDE
        else Capability.WORKSPACE_DELETE
    )


# 单份展示内容的上限. 超了截断并标注 —— 审批界面滚不完 10 MB, 而"已截断"这三个字本身
# 就是用户需要知道的信息: 他看到的不是全部.
_MAX_PREVIEW_CHARS = 64 * 1024


def _preview(path: str, content: str) -> ContentPreview:
    if len(content) <= _MAX_PREVIEW_CHARS:
        return ContentPreview(path=path, content=content)
    return ContentPreview(
        path=path, content=content[:_MAX_PREVIEW_CHARS], truncated=True
    )
