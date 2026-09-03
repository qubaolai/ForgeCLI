"""fs_read: 读一个文件 (ADR-0004 §14).

上界是 WORKSPACE_READ + EXTERNAL_READ, 但**每次调用只声明其中一个**: 读工作区内文件是
WORKSPACE_READ, 读工作区外文件是 EXTERNAL_READ. 这个区分是整套解耦的关键示例 —— 安全
模块不认识 "fs_read" 这个名字, 它只看到"这次要读区外路径", 于是按 mode 预算落
ASK. 读 ~/.ssh/id_rsa 和读 src/main.py 因此得到不同待遇, 而工具本身没有一行策略代码.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import NamedTuple

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import (
    emit_text,
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
    ArtifactPolicy,
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["ReadFileTool"]

_SPEC = ToolSpec(
    name="fs_read",
    version="3",
    title="读取文件",
    description=(
        "读取一个文件的文本内容. 默认读全文; "
        "大文件可以传 offset (从第几行开始, 从 1 起) 与 limit (读多少行) 只取一段, "
        "返回时会附带这一段在全文中的位置. 超长内容会截断并归档.\n"
        "已经知道是哪个文件时才用它. 要找一段文字出现在哪些地方, 走 search_text —— "
        "不要把候选文件逐个读回来自己扫.\n"
        "**手上有行号就按行号读**: find_definition 与 search_text 都带路径与行号. "
        "拿到之后传 offset 与 limit 只取那一段 (比如 offset=40 limit=30), 不必把整个"
        "文件读回来 —— 一个类常常几百行, 你要看的只有十几行."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1},
            "max_bytes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8 * 1024 * 1024,
            },
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
    # 全库唯一一个把正文带进会话窗口的工具 (ADR-0041 决策 6): 改代码之前必须看到原文,
    # 而"先 artifact_read 取回来再改"是白花一次调用. 其余工具的判据已经在 summary
    # 与 data 里, 正文只在需要细节时取.
    body_in_window=True,
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
        source_facts = context.filesystem.facts(facts.realpath)
        if source_facts.kind is not PathKind.FILE:
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"无法读取最终目标: {facts.realpath}",
                field_path="path",
            )
        scope = context.scope_of(facts.realpath)
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "path": facts.realpath,
                    "offset": request.arguments.get("offset"),
                    "limit": request.arguments.get("limit"),
                    "max_bytes": request.arguments.get("max_bytes"),
                    "source_size": source_facts.size,
                    "source_state": path_state_token(source_facts),
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
        if path_state_token(context.filesystem.facts(path)) != str(
            plan.normalized_input["source_state"]
        ):
            message = f"文件在计划生成后发生变化，已拒绝读取: {path}"
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=_SPEC.name,
                status=ToolResultStatus.TOOL_ERROR,
                summary=f"读取失败: {path} 在计划生成后发生变化",
                data={"path": path},
                error=ToolError(code="target_changed", message=message, retryable=True),
            )
        requested = _positive(plan.normalized_input.get("max_bytes"))
        ceiling = min(requested or limits.max_artifact_bytes, limits.max_artifact_bytes)
        text = context.filesystem.read_text(path, max_bytes=ceiling)
        raw_size = plan.normalized_input.get("source_size", 0)
        source_size = (
            raw_size
            if isinstance(raw_size, int) and not isinstance(raw_size, bool)
            else 0
        )
        window = _slice(
            text,
            offset=_positive(plan.normalized_input.get("offset")),
            limit=_positive(plan.normalized_input.get("limit")),
            complete=source_size <= ceiling,
            bytes_read=min(ceiling, source_size),
            source_size=source_size,
        )
        emitted = emit_text(
            window.text,
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="read_file",
        )
        line_count = len(window.text.splitlines())
        truncated = any(part.truncated for part in emitted.parts)
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            summary=(
                f"读了 {path}, {line_count} 行"
                + (", 已截断" if truncated else ", 未截断")
            ),
            data={
                "path": path,
                "lines": line_count,
                "bytes": emitted.bytes_out,
                "truncated": truncated,
            },
            # 位置说明单独一个 part, 不拼进正文: 拼进去模型照抄一段内容当 old_string 时
            # 会把它一起抄走, 于是 fs_apply_patch 的 FIND 段 逐字比对必然对不上.
            content_parts=emitted.parts + window.notes,
            artifacts=emitted.artifacts,
            metrics=ToolMetrics(bytes_out=emitted.bytes_out),
            # 只有这里填得出来源身份 (ADR-0032 决策 3): path 是 realpath, source_state
            # 是 perform 入口刚复核过的那一个 token. 拿它去重, 与 ADR-0027 的执行前
            # 复核用的是同一个判据.
            provenance=replace(
                emitted.provenance,
                source_path=path,
                source_state=str(plan.normalized_input["source_state"]),
            ),
        )


class _Window(NamedTuple):
    text: str
    notes: tuple[ContentPart, ...]


def _positive(raw: object) -> int | None:
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return None


def _slice(
    text: str,
    *,
    offset: int | None,
    limit: int | None,
    complete: bool = True,
    bytes_read: int = 0,
    source_size: int = 0,
) -> _Window:
    """按行取一段, 并如实说明取的是哪一段.

    不报位置的话, 模型拿到的是一段没有坐标的文本: 它无从判断上面还有没有内容, 也就
    容易把"这一段里没有"当成"这个文件里没有".
    """
    if offset is None and limit is None:
        notes = () if complete else (_incomplete_note(bytes_read, source_size),)
        return _Window(text, notes)
    lines = text.splitlines(keepends=True)
    total = len(lines)
    requested = offset or 1
    if requested > total:
        # 报**请求的** offset 而不是夹紧后的值: 用户和模型要对上的是自己传进来的数,
        # 看到一个自己没写过的行号只会以为工具算错了.
        note = (
            f"[第 {requested} 行超出已读取前缀；当前只读到 {total} 行，"
            "不能据此判断全文末尾]"
            if not complete
            else f"[第 {requested} 行超出文件末尾, 全文共 {total} 行]"
        )
        return _Window("", (ContentPart(text=note),))
    start = requested - 1
    stop = min(start + limit, total) if limit is not None else total
    note = (
        f"[以上是第 {start + 1}-{stop} 行；仅扫描了文件前缀，全文行数未知]"
        if not complete
        else f"[以上是第 {start + 1}-{stop} 行, 全文共 {total} 行]"
    )
    result_notes: list[ContentPart] = [ContentPart(text=note)]
    if not complete:
        result_notes.append(_incomplete_note(bytes_read, source_size))
    return _Window("".join(lines[start:stop]), tuple(result_notes))


def _incomplete_note(bytes_read: int, source_size: int) -> ContentPart:
    return ContentPart(
        text=(
            f"[内容不完整：读取了前 {bytes_read} 字节，文件共 {source_size} 字节；"
            "artifact 也只包含这段前缀]"
        )
    )
