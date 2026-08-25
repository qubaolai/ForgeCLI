"""fs_apply_patch: 一个信封替掉五个写工具 (ADR-0029 规则三 C 类).

原先是 `fs_create_file` / `fs_edit_file` / `fs_delete` / `fs_create_directory` /
`fs_move` 五个入口. 合并的理由不是"少几个文件", 而是**契约面**与**审批粒度**:

- 一个信封天然对应**一次审批, 一个恢复点**. 五个工具时, 一次跨文件的逻辑改动会产生
  四五次独立审批, 用户逐条点头却看不到整体.
- 五个入口意味着五套 prepare / plan / content_previews 逻辑, 那是 865 行的由来.
- 未来所有写入场景的变化都在信封语法里表达, **不占新工具位, 不加新参数**.

`fs_create_directory` 没有对应的段: `*** NEW` 会按需建父目录, 于是那个操作本身消失了
(ADR-0029 约束 1 只列了新建 / 更新 / 删除 / 移动四种).

**安全性质一条不减** (ADR-0029): 每个文件段各自绑定 `expected_state`, 计划生成后文件
变了就拒绝写入; write / delete / move 目标逐项进 `PlanEffects`, 恢复层照常逐项建点;
受保护路径, Hard Deny 与围栏边界不变.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from forgecli.application.tools.builtin.base import path_state_token, validate_arguments
from forgecli.application.tools.builtin.patch_apply import (
    apply_replacements,
    delete_targets,
)
from forgecli.application.tools.builtin.patch_envelope import (
    DeleteFile,
    MoveFile,
    NewFile,
    PatchSection,
    PatchSyntaxError,
    UpdateFile,
    parse_envelope,
)
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathFacts, PathKind
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import (
    ContentPreview,
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
    ToolMetrics,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import TargetDeclarationAbility, ToolSpec
from forgecli.shared.cancellation import CancelToken

__all__ = ["ApplyPatchTool"]

_SPEC = ToolSpec(
    name="fs_apply_patch",
    version="1",
    title="按补丁信封改文件",
    description=(
        "用一个补丁信封完成本次全部文件改动: 新建, 更新, 删除, 移动. "
        "一个信封 = 一次审批 = 一个恢复点, 所以一次逻辑改动请写在同一个信封里, "
        "不要拆成多次调用.\n"
        "\n"
        "标记必须独占一行并从行首开始:\n"
        "\n"
        "*** UPDATE 路径\n"
        "*** FIND\n"
        "<照抄文件里的原文>\n"
        "*** REPLACE\n"
        "<替换成什么>\n"
        "(同一个 UPDATE 段里 FIND/REPLACE 可以重复多次)\n"
        "\n"
        "*** NEW 路径          新建文件, 之后到下一个标记之前的原文就是全部内容; "
        "父目录按需自动创建; 目标已存在时失败\n"
        "*** DELETE 路径       删除文件或整个目录\n"
        "*** MOVE 源 -> 目标   移动或重命名, 目标已存在时失败\n"
        "\n"
        "同一片段要在多处替换时写 `*** FIND ALL`. "
        "FIND 段照抄文件内容即可: 行尾空白, 换行符与整块统一的缩进偏移会自动对齐, "
        "定位失败时会把文件里的原文回给你. FIND 必须在文件中唯一命中, "
        "命中多处请扩大片段. 正文逐字, 不要加 +/- 前缀. "
        "正文末尾的换行属于分隔符, 想保留末尾换行就多空一行."
    ),
    input_schema={
        "type": "object",
        "properties": {"patch": {"type": "string"}},
        "required": ["patch"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {"applied": {"type": "integer"}},
    },
    declared_capabilities=frozenset(
        {
            Capability.WORKSPACE_WRITE,
            Capability.WORKSPACE_DELETE,
            Capability.PATH_MOVE,
            Capability.EXTERNAL_WRITE,
        }
    ),
    # 删目录时 prepare 会展开成逐个文件并给出 FORGE_EXPANDED, STATIC 只允许 STATIC.
    target_declaration_ability=TargetDeclarationAbility.EXPANDABLE,
    default_timeout_seconds=30.0,
)


@dataclass(frozen=True)
class _Operation:
    """一个已经算好的文件操作. 内容在 prepare 阶段就定死, perform 不再重算."""

    kind: str
    path: str
    content: str = ""
    source: str = ""
    targets: tuple[str, ...] = ()
    expected: tuple[tuple[str, str], ...] = ()
    directories: tuple[str, ...] = ()
    note: str = ""


@dataclass
class _Planned:
    operations: list[_Operation] = field(default_factory=list)
    previews: list[ContentPreview] = field(default_factory=list)
    write_paths: list[str] = field(default_factory=list)
    delete_paths: list[str] = field(default_factory=list)
    move_pairs: list[MovePair] = field(default_factory=list)
    expanded: bool = False


class ApplyPatchTool(Tool):
    def __init__(
        self,
        creator: Callable[[str, str], None],
        writer: Callable[[str, str], None],
        remover: Callable[[str], None],
        mover: Callable[[str, str], None],
        maker: Callable[[str], None],
    ) -> None:
        # 新建与覆写的落盘方式不同, 不能共用一个 writer: 新建要让内核原子拒绝已存在的
        # 目标, 覆写要保留原文件的 mode. 合并成一个之后, 一次 NEW 会静默覆盖同名文件.
        self._create = creator
        self._write = writer
        self._remove = remover
        self._move = mover
        self._mkdir = maker

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    # ---- prepare ----

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(_SPEC, request.arguments)
        if invalid is not None:
            return invalid
        raw = request.arguments.get("patch")
        if not isinstance(raw, str) or not raw.strip():
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="patch 必须是非空字符串",
                field_path="patch",
            )
        try:
            sections = parse_envelope(raw)
        except PatchSyntaxError as error:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=f"补丁信封解析失败 —— {error.described()}",
                field_path="patch",
            )

        planned = _Planned()
        for section in sections:
            failed = self._plan_section(section, planned, context)
            if failed is not None:
                return failed

        touched = (
            *planned.write_paths,
            *planned.delete_paths,
            *(pair.target for pair in planned.move_pairs),
        )
        scope = context.scope_for_write_all(touched)
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            # 替换在 prepare 就算完, 计划里存的是**最终内容**: 裁决与审批绑定的是
            # "文件会变成什么样", 执行阶段不必重读文件, 少一个 TOCTOU 窗口.
            normalized_input=MappingProxyType(
                {"operations": tuple(_as_mapping(op) for op in planned.operations)}
            ),
            content_previews=tuple(planned.previews),
            capabilities=_capabilities_of(planned, scope),
            effects=PlanEffects(
                write_paths=tuple(planned.write_paths),
                delete_paths=tuple(planned.delete_paths),
                move_pairs=tuple(planned.move_pairs),
            ),
            target_resolution=(
                TargetResolution.FORGE_EXPANDED
                if planned.expanded
                else TargetResolution.STATIC
            ),
            workspace_scope=scope,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def _plan_section(
        self, section: PatchSection, planned: _Planned, context: ExecutionContext
    ) -> PreparationError | None:
        if isinstance(section, NewFile):
            return _plan_new(section, planned, context)
        if isinstance(section, UpdateFile):
            return _plan_update(section, planned, context)
        if isinstance(section, DeleteFile):
            return _plan_delete(section, planned, context)
        return _plan_move(section, planned, context)

    # ---- perform ----

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        operations = plan.normalized_input["operations"]
        assert isinstance(operations, tuple)
        applied: list[str] = []
        notes: list[str] = []
        for raw in operations:
            assert isinstance(raw, Mapping)
            stale = _stale_target(raw, context)
            if stale is not None:
                return _stale_result(plan, stale, applied)
            try:
                self._apply(raw)
            except OSError as exc:
                return _failed_result(plan, raw, exc, applied)
            applied.append(_describe(raw))
            note = str(raw.get("note", ""))
            if note:
                notes.append(f"{raw['path']}: {note}")

        lines = [f"已应用 {len(applied)} 处改动:", *(f"  {item}" for item in applied)]
        if notes:
            # 做过容差必须说出来: 模型手里那份 FIND 与文件并不一致, 不告诉它, 下一次
            # 它还会照着自己那份去拼, 而下一次未必还落在容差范围内.
            lines.extend(("对齐说明:", *(f"  {note}" for note in notes)))
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text="\n".join(lines)),),
            metrics=ToolMetrics(bytes_out=len("\n".join(lines).encode("utf-8"))),
            workspace_mutated=True,
        )

    def _apply(self, raw: Mapping[str, object]) -> None:
        kind = str(raw["kind"])
        if kind in {"create", "update"}:
            directories = raw.get("directories", ())
            assert isinstance(directories, Sequence)
            for directory in directories:
                self._mkdir(str(directory))
            write = self._create if kind == "create" else self._write
            write(str(raw["path"]), str(raw["content"]))
        elif kind == "delete":
            targets = raw["targets"]
            assert isinstance(targets, Sequence)
            for target in reversed(tuple(targets)):
                self._remove(str(target))
        else:
            self._move(str(raw["source"]), str(raw["path"]))


# ---- 各段的计划 ----


def _plan_new(
    section: NewFile, planned: _Planned, context: ExecutionContext
) -> PreparationError | None:
    resolved = _resolve(section.path, context, f"第 {section.index} 段 (NEW)")
    if isinstance(resolved, PreparationError):
        return resolved
    absolute, facts = resolved
    if facts.exists:
        return _error(
            f"第 {section.index} 段: 目标已存在, 新建会覆盖它: {absolute}. "
            "要改已有文件请用 *** UPDATE."
        )
    content = section.content
    if content and not content.endswith("\n"):
        # 绝大多数文本文件以换行结尾, 而信封语法把末尾换行当分隔符. 补上并在结果里说明,
        # 好过让每个新建文件都缺一个换行 —— 那种缺失 diff 里看得见, 但模型不会想到.
        content += "\n"
    directories = _missing_parents(absolute, context)
    planned.operations.append(
        _Operation(
            kind="create",
            path=absolute,
            content=content,
            expected=((absolute, path_state_token(facts)),),
            directories=directories,
            note="已补上末尾换行" if content != section.content else "",
        )
    )
    planned.previews.append(ContentPreview(path=absolute, content=content))
    planned.write_paths.extend((*directories, absolute))
    return None


def _plan_update(
    section: UpdateFile, planned: _Planned, context: ExecutionContext
) -> PreparationError | None:
    resolved = _resolve(section.path, context, f"第 {section.index} 段 (UPDATE)")
    if isinstance(resolved, PreparationError):
        return resolved
    absolute, facts = resolved
    if not facts.exists:
        return _error(
            f"第 {section.index} 段: 要更新的文件不存在: {absolute}. "
            "新建请用 *** NEW."
        )
    if facts.kind is not PathKind.FILE:
        return _error(f"第 {section.index} 段: 不是普通文件: {absolute}")

    edited = apply_replacements(absolute, section, context)
    if isinstance(edited, PreparationError):
        return edited
    planned.operations.append(
        _Operation(
            kind="update",
            path=absolute,
            content=edited.content,
            expected=((absolute, path_state_token(facts)),),
            note=edited.note,
        )
    )
    planned.previews.append(ContentPreview(path=absolute, content=edited.content))
    planned.write_paths.append(absolute)
    return None


def _plan_delete(
    section: DeleteFile, planned: _Planned, context: ExecutionContext
) -> PreparationError | None:
    resolved = _resolve(section.path, context, f"第 {section.index} 段 (DELETE)")
    if isinstance(resolved, PreparationError):
        return resolved
    absolute, facts = resolved
    if not facts.exists:
        return _error(f"第 {section.index} 段: 要删除的路径不存在: {absolute}")
    if facts.kind is PathKind.OTHER:
        # 设备, FIFO, socket: 删它们的后果与删普通文件完全不同, 不在本工具范围内.
        return _error(
            f"第 {section.index} 段: 不是普通文件或目录, 拒绝删除: {absolute}"
        )

    if facts.kind is PathKind.DIRECTORY:
        expanded = delete_targets(context, absolute)
        if isinstance(expanded, PreparationError):
            return expanded
        targets = expanded
        planned.expanded = True
    else:
        targets = (absolute,)
    planned.operations.append(
        _Operation(
            kind="delete",
            path=absolute,
            targets=targets,
            expected=tuple(
                (target, path_state_token(context.filesystem.facts(target)))
                for target in targets
            ),
        )
    )
    planned.delete_paths.extend(targets)
    return None


def _plan_move(
    section: MoveFile, planned: _Planned, context: ExecutionContext
) -> PreparationError | None:
    where = f"第 {section.index} 段 (MOVE)"
    origin = _resolve(section.source, context, where)
    if isinstance(origin, PreparationError):
        return origin
    source, source_facts = origin
    if not source_facts.exists:
        return _error(f"{where}: 源路径不存在: {source}")
    if source_facts.kind is not PathKind.FILE:
        # 目录移动要把整棵树的两端都算进冲突域与恢复清单, 不在本段范围内.
        return _error(f"{where}: 只支持移动普通文件: {source}")

    destination = _resolve(section.target, context, where)
    if isinstance(destination, PreparationError):
        return destination
    target, target_facts = destination
    if target_facts.exists:
        # 不静默覆盖: 覆盖是两次写 (删目标 + 写目标), 与"移动"是两回事, 恢复层存的
        # preimage 也不一样. 要覆盖请先显式 *** DELETE.
        return _error(f"{where}: 目标已存在, 不会覆盖: {target}")

    parent = str(Path(target).parent)
    if context.filesystem.facts(parent).kind is not PathKind.DIRECTORY:
        return _error(f"{where}: 目标父目录不存在: {parent}")
    planned.operations.append(
        _Operation(
            kind="move",
            path=target,
            source=source,
            expected=((source, path_state_token(source_facts)),),
        )
    )
    planned.move_pairs.append(MovePair(source=source, target=target))
    return None


# ---- 共用 ----


def _resolve(
    raw: str, context: ExecutionContext, where: str
) -> tuple[str, PathFacts] | PreparationError:
    if not raw.strip():
        return _error(f"{where}: 路径是空的")
    absolute = context.resolve(raw)
    facts = context.filesystem.facts(absolute)
    if facts.is_symlink:
        return _error(f"{where}: 不跟随也不修改符号链接: {absolute}")
    return facts.realpath, facts


def _missing_parents(absolute: str, context: ExecutionContext) -> tuple[str, ...]:
    """从外到内列出需要创建的父目录.

    `fs_create_directory` 因此不需要存在: 新建文件时按需建父目录是同一个动作的一部分,
    单独一个工具位换不来任何东西 (ADR-0029 规则一).
    """
    missing: list[str] = []
    current = Path(absolute).parent
    while not context.filesystem.facts(str(current)).exists:
        missing.append(str(current))
        if current.parent == current:
            break
        current = current.parent
    return tuple(reversed(missing))


def _capabilities_of(planned: _Planned, scope: WorkspaceScope) -> frozenset[Capability]:
    outside = scope is WorkspaceScope.OUTSIDE
    capabilities: set[Capability] = set()
    if planned.write_paths:
        capabilities.add(
            Capability.EXTERNAL_WRITE if outside else Capability.WORKSPACE_WRITE
        )
    if planned.delete_paths:
        capabilities.add(
            Capability.EXTERNAL_WRITE if outside else Capability.WORKSPACE_DELETE
        )
    if planned.move_pairs:
        capabilities.add(Capability.PATH_MOVE)
        if outside:
            capabilities.add(Capability.EXTERNAL_WRITE)
    return frozenset(capabilities)


def _as_mapping(operation: _Operation) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "kind": operation.kind,
            "path": operation.path,
            "content": operation.content,
            "source": operation.source,
            "targets": operation.targets,
            "expected": operation.expected,
            "directories": operation.directories,
            "note": operation.note,
        }
    )


def _stale_target(raw: Mapping[str, object], context: ExecutionContext) -> str | None:
    expected = raw.get("expected", ())
    assert isinstance(expected, Sequence)
    for entry in expected:
        path, token = entry
        if path_state_token(context.filesystem.facts(str(path))) != str(token):
            return str(path)
    return None


def _describe(raw: Mapping[str, object]) -> str:
    kind = str(raw["kind"])
    if kind == "move":
        return f"移动 {raw['source']} -> {raw['path']}"
    if kind == "delete":
        targets = raw["targets"]
        assert isinstance(targets, Sequence)
        count = len(targets)
        suffix = f" ({count} 项)" if count > 1 else ""
        return f"删除 {raw['path']}{suffix}"
    content = str(raw["content"])
    return f"写入 {raw['path']} ({len(content)} 字符, {len(content.splitlines())} 行)"


def _error(message: str) -> PreparationError:
    return PreparationError(
        code=PreparationErrorCode.INVALID_INPUT, message=message, field_path="patch"
    )


def _stale_result(plan: ToolPlan, path: str, applied: list[str]) -> ToolResult:
    done = f" 已完成 {len(applied)} 处, 未回滚." if applied else ""
    return ToolResult(
        invocation_id=plan.plan_id,
        tool_name=_SPEC.name,
        status=ToolResultStatus.TOOL_ERROR,
        content_parts=(
            ContentPart(text=f"目标在计划生成后发生变化, 已拒绝写入: {path}.{done}"),
        ),
        error=ToolError(code="target_changed", message=path),
        workspace_mutated=bool(applied),
    )


def _failed_result(
    plan: ToolPlan, raw: Mapping[str, object], exc: OSError, applied: list[str]
) -> ToolResult:
    done = f" 已完成 {len(applied)} 处, 未回滚." if applied else ""
    return ToolResult(
        invocation_id=plan.plan_id,
        tool_name=_SPEC.name,
        status=ToolResultStatus.TOOL_ERROR,
        error=ToolError(code="apply_failed", message=f"{raw['path']}: {exc}.{done}"),
        workspace_mutated=bool(applied),
    )
