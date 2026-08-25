"""git.read: 只读 git 查询 (ADR-0004 §14).

它声明 SPAWN_PROCESS 但**不**声明 EXECUTE_SHELL: 子命令来自固定白名单, argv 直接交给
执行器, 不经过任何 shell 解释器, 因此没有管道, 重定向和命令替换的攻击面. 这也是它能出
现在 plan 档目录里的原因 —— 窄上界换来的.

白名单在这里是**机制**而不是策略: 它约束的是"这个工具能做什么", 不是"这次调用允不允许".
`git log` 是否允许仍由安全模块按能力和路径裁决.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import (
    emit_text,
    resolve_executable,
    validate_arguments,
)
from forgecli.application.tools.command_executor import CommandExecutor, CommandRequest
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
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["READ_ONLY_SUBCOMMANDS", "GitReadTool"]


@dataclass(frozen=True)
class _ReadOnlyForm:
    """一个只读子命令允许的参数形状.

    这是**白名单**: 没列出的选项一律拒绝. 早先用的是禁止清单 (`-d`, `--delete`,
    `drop` ...), 方向就错了 —— 精确 token 比对挡不住 `-df` 这样的合并写法, 也挡不住
    清单里没想到的写操作 (`git branch new-name` 建 ref, `git remote add` 改
    `.git/config`, 裸 `git stash` 动工作树). 更要紧的是 git 有一批"读命令里的执行
    入口": `git diff --ext-diff` / `--textconv` 会跑外部程序, `git diff --output=F`
    会写文件, `git -c core.pager=...` 能指定任意命令. 它们不在任何白名单里, 因此自动
    被拒 —— 这正是白名单的意义.

    **口径必须与 domain/security/shell/commands.py 的 GIT_READ_ONLY_SUBCOMMANDS 一致.**
    两处是同一类知识的两份, 但分属 tools 与 security, check_arch.py 禁止互相 import
    (ADR-0004 §2), 因此只能靠这条交叉注释绑住. 这里更严 (还要管选项形状), 那边只回答
    "这个子命令读还是写" —— 允许这里更严, 不允许那边更严.
    """

    flags: frozenset[str] = frozenset()
    # 带值的选项. 同时接受 `--x=y` 与 `--x y`, 后者要跳过它的值.
    value_flags: frozenset[str] = frozenset()
    # 允许的**第一个**位置参数关键字. 非空时第一个位置参数必须落在里面.
    words: frozenset[str] = frozenset()
    # 是否允许任意 revision / pathspec 作为位置参数.
    free_positional: bool = False
    # 是否必须给出 words 里的一个. 裸 `git stash` 会推栈, 所以它是必须的.
    require_word: bool = False


_COMMON_FLAGS = frozenset({"--no-color", "-q", "--quiet"})

# 只读子命令. 不含 add/commit/push/checkout/reset/clean —— 那些是写操作, 应当由
# fs.* 或 shell.run 走各自的裁决路径, 不能借"git 是只读工具"的壳混进来.
_READ_ONLY_FORMS: dict[str, _ReadOnlyForm] = {
    "status": _ReadOnlyForm(
        flags=_COMMON_FLAGS
        | {
            "-s",
            "--short",
            "-b",
            "--branch",
            "--porcelain",
            "-uall",
            "-uno",
            "-unormal",
        },
        value_flags=frozenset({"--untracked-files"}),
        free_positional=True,
    ),
    "diff": _ReadOnlyForm(
        flags=_COMMON_FLAGS
        | {
            "--stat",
            "--numstat",
            "--shortstat",
            "--summary",
            "--name-only",
            "--name-status",
            "--cached",
            "--staged",
            "-p",
            "--patch",
            "-w",
            "--ignore-all-space",
            "--ignore-space-change",
            "--find-renames",
            "-M",
            "--no-ext-diff",
            "--no-textconv",
        },
        value_flags=frozenset({"-U", "--unified", "--diff-filter"}),
        free_positional=True,
    ),
    "log": _ReadOnlyForm(
        flags=_COMMON_FLAGS
        | {
            "--oneline",
            "--graph",
            "--decorate",
            "--no-decorate",
            "--stat",
            "--name-only",
            "--name-status",
            "--all",
            "--first-parent",
            "--no-merges",
            "--reverse",
            "--no-ext-diff",
        },
        value_flags=frozenset(
            {
                "-n",
                "--max-count",
                "--skip",
                "--since",
                "--until",
                "--author",
                "--committer",
                "--grep",
                "--format",
                "--pretty",
                "--date",
            }
        ),
        free_positional=True,
    ),
    "show": _ReadOnlyForm(
        flags=_COMMON_FLAGS
        | {
            "--stat",
            "--name-only",
            "--name-status",
            "-s",
            "--no-patch",
            "--no-ext-diff",
            "--no-textconv",
        },
        value_flags=frozenset({"--format", "--pretty", "-U", "--unified"}),
        free_positional=True,
    ),
    "blame": _ReadOnlyForm(
        flags=_COMMON_FLAGS | {"-l", "-s", "-w", "--line-porcelain", "--porcelain"},
        value_flags=frozenset({"-L"}),
        free_positional=True,
    ),
    # 位置参数一律不收: `git branch <name>` 会创建 ref, 那是写操作.
    "branch": _ReadOnlyForm(
        flags=_COMMON_FLAGS
        | {
            "--list",
            "-l",
            "-a",
            "--all",
            "-r",
            "--remotes",
            "-v",
            "-vv",
            "--verbose",
            "--show-current",
        },
    ),
    # `show` 可能访问网络并调用凭证帮助器；只保留纯本地的 get-url。
    "remote": _ReadOnlyForm(
        flags=_COMMON_FLAGS | {"-v", "--verbose"},
        words=frozenset({"get-url"}),
        free_positional=True,
    ),
    # 裸 `git stash` 等于 `git stash push`, 会改工作树与 index, 所以必须显式给关键字.
    "stash": _ReadOnlyForm(
        flags=_COMMON_FLAGS,
        words=frozenset({"list", "show"}),
        free_positional=True,
        require_word=True,
    ),
}

READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset(_READ_ONLY_FORMS)


def _reject_args(subcommand: str, args: list[str]) -> str | None:
    """按白名单校验参数. 返回拒绝原因, None 表示通过."""
    form = _READ_ONLY_FORMS[subcommand]
    positional_seen = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            # `--` 之后一律是 pathspec. 不允许自由位置参数的子命令到这里就该停.
            if not form.free_positional:
                return f"{subcommand} 不接受位置参数"
            break
        if arg.startswith("-"):
            base, separator, _ = arg.partition("=")
            if base in form.value_flags:
                if not separator:
                    index += 1  # 跳过它的值
                index += 1
                continue
            if base in form.flags or arg in form.flags:
                index += 1
                continue
            return f"{subcommand} 不允许选项 {arg}"
        if not positional_seen:
            positional_seen = True
            if form.words and arg not in form.words:
                return f"{subcommand} 只接受 {sorted(form.words)}, 不接受 {arg}"
            if not form.words and not form.free_positional:
                return f"{subcommand} 不接受位置参数"
            index += 1
            continue
        if not form.free_positional:
            return f"{subcommand} 不接受位置参数"
        index += 1
    if form.require_word and not positional_seen:
        return f"{subcommand} 必须给出 {sorted(form.words)} 之一"
    return None


_SPEC = ToolSpec(
    name="git.read",
    version="2",
    title="读取 git 状态",
    description=(
        "执行只读 git 子命令: status/diff/log/show/blame/branch/remote/stash list."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "subcommand": {"type": "string"},
            "args": {
                "type": "array",
                "maxItems": 128,
                "items": {"type": "string", "maxLength": 4096},
            },
        },
        "required": ["subcommand"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"stdout": {"type": "string"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_READ, Capability.SPAWN_PROCESS}
    ),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=30.0,
)


class GitReadTool(Tool):
    def __init__(
        self,
        executor: CommandExecutor,
        governor: ResourceGovernor,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        self._executor = executor
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
        subcommand = str(request.arguments.get("subcommand", "")).strip()
        if subcommand not in READ_ONLY_SUBCOMMANDS:
            return PreparationError(
                code=PreparationErrorCode.UNSUPPORTED_REQUEST,
                message=f"git.read 只支持只读子命令: {sorted(READ_ONLY_SUBCOMMANDS)}",
                field_path="subcommand",
            )
        raw_args = request.arguments.get("args", [])
        args = [str(item) for item in raw_args] if isinstance(raw_args, list) else []
        rejected = _reject_args(subcommand, args)
        if rejected is not None:
            return PreparationError(
                code=PreparationErrorCode.UNSUPPORTED_REQUEST,
                message=f"git.read 只接受确定只读的参数: {rejected}",
                field_path="args",
            )
        executable = resolve_executable("git", context)
        if executable is None:
            return PreparationError(
                code=PreparationErrorCode.UNSUPPORTED_REQUEST,
                message="受控 PATH 中找不到 git",
            )
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "executable": executable,
                    "subcommand": subcommand,
                    "args": args,
                }
            ),
            capabilities=frozenset(
                {Capability.WORKSPACE_READ, Capability.SPAWN_PROCESS}
            ),
            effects=PlanEffects(read_paths=(context.primary_root,), child_process=True),
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
        raw_args = plan.normalized_input.get("args", [])
        args = (
            tuple(str(item) for item in raw_args) if isinstance(raw_args, list) else ()
        )
        outcome = self._executor.run(
            CommandRequest(
                argv=(
                    str(plan.normalized_input["executable"]),
                    "--no-pager",
                    "-c",
                    "core.fsmonitor=false",
                    str(plan.normalized_input["subcommand"]),
                    *_mandatory_args(str(plan.normalized_input["subcommand"])),
                    *args,
                ),
                cwd=context.primary_root,
                environment=_git_environment(context.environment),
                timeout_seconds=limits.timeout_seconds,
                max_output_bytes=limits.max_artifact_bytes,
                # 围栏边界随请求走 (ADR-0030 决策 1). 漏传的后果是执行器 fail closed,
                # 不是"没策略就不围".
                fence=context.fence,
            ),
            cancel,
        )
        text = _render(outcome.stdout, outcome.stderr, outcome.truncated)
        emitted = emit_text(
            text,
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="git_read",
        )
        metrics = ToolMetrics(
            duration_seconds=outcome.duration_seconds,
            exit_code=outcome.exit_code,
            bytes_out=emitted.bytes_out,
            child_process_count=outcome.child_process_count,
        )
        if outcome.succeeded:
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=_SPEC.name,
                status=ToolResultStatus.OK,
                content_parts=emitted.parts,
                artifacts=emitted.artifacts,
                metrics=metrics,
                provenance=emitted.provenance,
            )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=_status_of(outcome.timed_out, outcome.cancelled),
            content_parts=emitted.parts,
            artifacts=emitted.artifacts,
            metrics=metrics,
            provenance=emitted.provenance,
            error=ToolError(
                code="git_failed",
                message=outcome.failure or (outcome.stderr.strip() or "git 执行失败"),
                retryable=False,
            ),
        )


def _status_of(timed_out: bool, cancelled: bool) -> ToolResultStatus:
    if timed_out:
        return ToolResultStatus.TIMEOUT
    if cancelled:
        return ToolResultStatus.CANCELLED
    return ToolResultStatus.TOOL_ERROR


def _mandatory_args(subcommand: str) -> tuple[str, ...]:
    if subcommand in {"diff", "show"}:
        return ("--no-ext-diff", "--no-textconv")
    if subcommand == "log":
        return ("--no-ext-diff",)
    return ()


def _git_environment(base: Mapping[str, str]) -> Mapping[str, str]:
    environment = dict(base)
    environment.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    return MappingProxyType(environment)


def _render(stdout: str, stderr: str, truncated: bool) -> str:
    body = stdout
    if stderr:
        body = f"{body}\n[stderr]\n{stderr}" if body else stderr
    if truncated:
        note = "[输出已达到执行器上限，stdout/stderr 与 artifact 都可能不完整]"
        body = f"{note}\n{body}" if body else note
    return body
