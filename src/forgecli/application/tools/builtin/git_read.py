"""git_read: 只读 git 查询 (ADR-0004 §14, ADR-0040 决策 4.4).

它**不起子进程**, 也不声明 SPAWN_PROCESS: 查询走 libgit2 的进程内调用 (`GitQueries`
端口), 没有 argv, 没有 shell, 也没有 git CLI 那些"读命令里的执行入口".

原先不是这样. 原先它拼一条 `git <子命令> <参数...>` 交给执行器, 于是必须自带一张 CLI
参数白名单来挡住 `--ext-diff` / `--textconv` (跑外部程序), `--output=F` (写文件) 和
`-c core.pager=...` (指定任意命令). 那张表有 8 个子命令, 上百个选项, 而且按定义永远不
完整 —— git 每加一个选项它就旧一点, 漏一个就是一个执行入口.

换成结构化参数之后, 那张表不存在了: 模型给不出"一个选项", 只能给出 schema 里列着的
字段. `staged` 是布尔, `max_count` 是有上下界的整数. 没有地方能塞进一个命令名.

## 输出是给模型读的文本

每种查询都渲染成短文本. 空结果一律有明确的一句话 ("工作区干净", "没有 stash"), 不返回
空串 —— 模型分不清"没有内容"和"这次调用失败了", 而这两件事该走不同的下一步.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from types import MappingProxyType

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import emit_text, validate_arguments
from forgecli.application.tools.git_queries import (
    GitBlameHunk,
    GitBranch,
    GitCommit,
    GitPatch,
    GitQueries,
    GitQueryError,
    GitRemote,
    GitStash,
    GitStatusEntry,
    GitUnsupported,
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

__all__ = ["QUERIES", "GitReadTool"]

# 支持的查询. 这是**封闭集**: 成员由这个工具的协议定义, 新增一个要同时写渲染
# (ADR-0040 决策 7 的封闭枚举, 不是那种穷举不完的开放表).
QUERIES: frozenset[str] = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "blame",
        "branches",
        "remotes",
        "stashes",
    }
)

_SPEC = ToolSpec(
    name="git_read",
    version="3",
    title="读取 git 状态",
    description=(
        "只读 git 查询: status/diff/log/show/blame/branches/remotes/stashes. "
        "进程内读取仓库, 不执行 git 命令."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "enum": sorted(QUERIES)},
            # diff
            "staged": {"type": "boolean"},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 25},
            # diff / log
            "paths": {
                "type": "array",
                "maxItems": 64,
                "items": {"type": "string", "maxLength": 4096},
            },
            # log / show
            "revision": {"type": "string", "maxLength": 256},
            "max_count": {"type": "integer", "minimum": 1, "maximum": 200},
            # blame
            "path": {"type": "string", "maxLength": 4096},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
            # branches
            "include_remote": {"type": "boolean"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"stdout": {"type": "string"}}},
    declared_capabilities=frozenset({Capability.WORKSPACE_READ}),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=30.0,
    action=ToolAction.READ,
)


class GitReadTool(Tool):
    def __init__(
        self,
        queries: GitQueries,
        governor: ResourceGovernor,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        self._queries = queries
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
        query = str(request.arguments.get("query", "")).strip()
        if query not in QUERIES:
            return PreparationError(
                code=PreparationErrorCode.UNSUPPORTED_REQUEST,
                message=f"git_read 只支持这些查询: {sorted(QUERIES)}",
                field_path="query",
            )
        if query == "blame" and not str(request.arguments.get("path", "")).strip():
            return PreparationError(
                code=PreparationErrorCode.UNSUPPORTED_REQUEST,
                message="blame 需要 path",
                field_path="path",
            )
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(dict(request.arguments)),
            capabilities=frozenset({Capability.WORKSPACE_READ}),
            # child_process=False 是这次改动的实质: 读 git 不再是"起一个进程".
            effects=PlanEffects(
                read_paths=(context.primary_root,), child_process=False
            ),
            target_resolution=TargetResolution.STATIC,
            workspace_scope=context.scope_of(context.primary_root),
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
        started = time.perf_counter()
        try:
            text = self._run(plan, context.primary_root)
        except GitUnsupported as exc:
            return self._failed(plan, "git_unsupported", str(exc), started)
        except GitQueryError as exc:
            return self._failed(plan, "git_failed", str(exc), started)
        emitted = emit_text(
            text,
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="git_read",
        )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=emitted.parts,
            artifacts=emitted.artifacts,
            metrics=ToolMetrics(
                duration_seconds=time.perf_counter() - started,
                bytes_out=emitted.bytes_out,
            ),
            provenance=emitted.provenance,
        )

    def _failed(
        self, plan: ToolPlan, code: str, message: str, started: float
    ) -> ToolResult:
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.TOOL_ERROR,
            metrics=ToolMetrics(duration_seconds=time.perf_counter() - started),
            error=ToolError(code=code, message=message, retryable=False),
        )

    def _run(self, plan: ToolPlan, root: str) -> str:
        arguments = plan.normalized_input
        query = str(arguments["query"])
        if query == "status":
            return _render_status(self._queries.status(root))
        if query == "diff":
            return _render_patch(
                self._queries.diff(
                    root,
                    staged=bool(arguments.get("staged", False)),
                    paths=_strings(arguments.get("paths")),
                    context_lines=_int_or(arguments.get("context_lines"), 3),
                )
            )
        if query == "log":
            return _render_log(
                self._queries.log(
                    root,
                    revision=_optional_text(arguments.get("revision")),
                    max_count=_int_or(arguments.get("max_count"), 20),
                    paths=_strings(arguments.get("paths")),
                )
            )
        if query == "show":
            commit, patch = self._queries.show(
                root, _optional_text(arguments.get("revision")) or "HEAD"
            )
            return _render_show(commit, patch)
        if query == "blame":
            return _render_blame(
                self._queries.blame(
                    root,
                    str(arguments["path"]),
                    start_line=_optional_int(arguments.get("start_line")),
                    end_line=_optional_int(arguments.get("end_line")),
                )
            )
        if query == "branches":
            return _render_branches(
                self._queries.branches(
                    root, include_remote=bool(arguments.get("include_remote", False))
                )
            )
        if query == "remotes":
            return _render_remotes(self._queries.remotes(root))
        return _render_stashes(self._queries.stashes(root))


# ---- 渲染 ----


def _render_status(entries: Sequence[GitStatusEntry]) -> str:
    if not entries:
        return "工作区干净, 没有未提交的改动."
    lines = [f"{entry.index}{entry.worktree} {entry.path}" for entry in entries]
    return "\n".join([f"{len(entries)} 个文件有改动:", *lines])


def _render_patch(patch: GitPatch) -> str:
    stats = patch.stats
    if stats.files_changed == 0:
        return "没有改动."
    header = f"{stats.files_changed} 个文件, " f"+{stats.insertions} -{stats.deletions}"
    return f"{header}\n\n{patch.text}" if patch.text else header


def _render_log(commits: Sequence[GitCommit]) -> str:
    if not commits:
        return "没有匹配的提交."
    return "\n".join(
        f"{c.short_id}  {c.committed_at}  {c.author}  {c.summary}" for c in commits
    )


def _render_show(commit: GitCommit, patch: GitPatch) -> str:
    header = (
        f"{commit.short_id}  {commit.committed_at}  {commit.author}\n{commit.summary}"
    )
    return f"{header}\n\n{_render_patch(patch)}"


def _render_blame(hunks: Sequence[GitBlameHunk]) -> str:
    if not hunks:
        return "没有 blame 信息 (文件是空的, 或者还没被提交过)."
    return "\n".join(
        f"{hunk.short_id}  {hunk.author}  "
        f"L{hunk.start_line}-{hunk.start_line + hunk.line_count - 1}"
        for hunk in hunks
    )


def _render_branches(branches: Sequence[GitBranch]) -> str:
    if not branches:
        return "没有分支 (仓库还没有提交)."
    return "\n".join(
        f"{'*' if branch.is_head else ' '} {branch.name}  {branch.short_id}"
        for branch in branches
    )


def _render_remotes(remotes: Sequence[GitRemote]) -> str:
    if not remotes:
        return "没有配置远端."
    return "\n".join(f"{remote.name}  {remote.url}" for remote in remotes)


def _render_stashes(stashes: Sequence[GitStash]) -> str:
    if not stashes:
        return "没有 stash."
    return "\n".join(
        f"stash@{{{stash.index}}}  {stash.short_id}  {stash.message}"
        for stash in stashes
    )


# ---- 参数取值 ----


def _strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw)


def _optional_text(raw: object) -> str | None:
    text = str(raw).strip() if raw is not None else ""
    return text or None


def _optional_int(raw: object) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _int_or(raw: object, fallback: int) -> int:
    """schema 已经限死了取值范围, 这里只是把 `object` 收窄回 int."""
    resolved = _optional_int(raw)
    return fallback if resolved is None else resolved
