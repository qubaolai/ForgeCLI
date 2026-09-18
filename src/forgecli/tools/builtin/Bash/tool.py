"""Bash (对应 BashTool/BashTool.tsx).

is_read_only 由 read_only_validation 回答, is_concurrency_safe 等于它; check_permissions
交给 bash_permissions; call 经 CommandExecutor 起子进程 —— 会进围栏的走 sandboxed
执行器, 不进的 (excludedCommands, dangerously_disable_sandbox) 走本地执行器.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from forgecli.core.execution.denial import blocked_paths
from forgecli.core.execution.environment import ShellLaunch
from forgecli.core.execution.fence import FencePolicy
from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionResult
from forgecli.core.sandbox import SandboxSettings
from forgecli.core.tool.names import BASH_TOOL_NAME
from forgecli.core.tool.tool import (
    CanUseToolFn,
    ProgressCallback,
    PromptOptions,
    SearchOrRead,
    Tool,
    ToolResult,
    ToolUseContext,
    ValidationResult,
)
from forgecli.execution.command_executor import CommandExecutor, CommandRequest
from forgecli.prompt import prompts
from forgecli.security.sandbox_rules import should_use_sandbox
from forgecli.security.shell.command_plan import ShellKind
from forgecli.tools.builtin.base import int_of, str_of, text_block
from forgecli.tools.builtin.Bash.bash_permissions import (
    bash_tool_has_permission,
    parse_for_permissions,
)
from forgecli.tools.builtin.Bash.destructive_command_warning import (
    get_destructive_command_warning,
)
from forgecli.tools.builtin.Bash.prompt import (
    DEFAULT_TIMEOUT_MS,
    MAX_TIMEOUT_MS,
    prompt,
)
from forgecli.tools.builtin.Bash.read_only_validation import check_read_only_constraints

__all__ = ["BashOutput", "BashTool"]

FenceFactory = Callable[[ToolUseContext], FencePolicy | None]

_MAX_OUTPUT_BYTES = 8 * 1024 * 1024

_INPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "The command to execute"},
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_TIMEOUT_MS,
            "description": f"Optional timeout in milliseconds (max {MAX_TIMEOUT_MS})",
        },
        "description": {
            "type": "string",
            "description": (
                "Clear, concise description of what this command does in active voice. "
                'Never use words like "complex" or "risk" in the description - just '
                "describe what it does."
            ),
        },
        "dangerously_disable_sandbox": {
            "type": "boolean",
            "description": (
                "Set this to true to dangerously override sandbox mode and run "
                "commands without sandboxing."
            ),
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class BashOutput:
    stdout: str
    stderr: str
    exit_code: int | None
    interrupted: bool
    timed_out: bool
    duration_seconds: float
    failure: str = ""
    truncated: bool = False
    # 输出里疑似被围栏拦下的路径 (ADR-0041 决策 9): 只在围栏立着时才算, 没围栏那句
    # Permission denied 只能是文件系统本身的权限, 说成围栏就是在骗模型.
    fence_blocked: tuple[str, ...] = ()
    # 模型能不能带 dangerously_disable_sandbox 重试: 决定围栏提示怎么说.
    can_disable_sandbox: bool = False


class BashTool(Tool):
    name = BASH_TOOL_NAME
    aliases = ("shell_run",)
    search_hint = "execute shell commands"
    input_schema = _INPUT_SCHEMA
    strict = True
    max_result_size_chars = 30_000

    def __init__(
        self,
        *,
        local: CommandExecutor,
        sandboxed: CommandExecutor,
        shell_launch: ShellLaunch,
        fence_factory: FenceFactory,
    ) -> None:
        self._local = local
        self._sandboxed = sandboxed
        self._launch = shell_launch
        self._fence_factory = fence_factory

    @property
    def shell_kind(self) -> ShellKind:
        return ShellKind(self._launch.dialect)

    def description(self, input: Mapping[str, object]) -> str:
        return str_of(input, "description") or "Run shell command"

    def prompt(self, options: PromptOptions) -> str:
        sandbox = SandboxSettings()
        if options.get_app_state is not None:
            sandbox = options.get_app_state().sandbox
        return prompt(sandbox)

    def user_facing_name(self, input: Mapping[str, object]) -> str:
        return self.name

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        command = str_of(input, "command").strip()
        if not command:
            return None
        first = command.split("\n", 1)[0]
        if len(first) > 100:
            return first[:97] + "..."
        return first

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        summary = self.get_tool_use_summary(input)
        if summary:
            return f"Running {summary}"
        return "Running command"

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        command = str_of(input, "command")
        if not command.strip():
            return False
        plan = parse_for_permissions(command, self.shell_kind, "")
        return check_read_only_constraints(plan).read_only

    def is_concurrency_safe(self, input: Mapping[str, object]) -> bool:
        return self.is_read_only(input)

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        return str_of(input, "command")

    def is_destructive(self, input: Mapping[str, object]) -> bool:
        return get_destructive_command_warning(str_of(input, "command")) is not None

    def is_search_or_read_command(
        self, input: Mapping[str, object]
    ) -> SearchOrRead | None:
        command = str_of(input, "command").strip()
        first = command.split(" ", 1)[0] if command else ""
        if first in ("grep", "rg", "find", "ag"):
            return SearchOrRead(is_search=True, is_read=False)
        if first in ("cat", "head", "tail", "less", "more"):
            return SearchOrRead(is_search=False, is_read=True)
        if first in ("ls", "tree", "du"):
            return SearchOrRead(is_search=False, is_read=False, is_list=True)
        return None

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        if not str_of(input, "command").strip():
            return ValidationResult(
                ok=False, message="command must not be empty", error_code=1
            )
        return ValidationResult(ok=True)

    def check_permissions(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> PermissionResult:
        return bash_tool_has_permission(input, context, shell_kind=self.shell_kind)

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        command = str_of(input, "command").strip()
        timeout_ms = int_of(input, "timeout") or DEFAULT_TIMEOUT_MS
        timeout_ms = min(timeout_ms, MAX_TIMEOUT_MS)
        sandbox = context.get_app_state().sandbox
        use_sandbox = should_use_sandbox(input, sandbox)
        executor = self._sandboxed if use_sandbox else self._local
        fence = self._fence_factory(context) if use_sandbox else None
        outcome = executor.run(
            CommandRequest(
                argv=(self._launch.program, *self._launch.args, command),
                cwd=context.cwd,
                environment=context.environment,
                timeout_seconds=timeout_ms / 1000.0,
                max_output_bytes=_MAX_OUTPUT_BYTES,
                fence=fence,
            ),
            context.abort,
        )
        blocked: tuple[str, ...] = ()
        if fence is not None and outcome.exit_code not in (0, None):
            blocked = _outside_fence(
                blocked_paths(outcome.stdout + "\n" + outcome.stderr), fence
            )
        return ToolResult(
            data=BashOutput(
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                exit_code=outcome.exit_code,
                interrupted=outcome.cancelled,
                timed_out=outcome.timed_out,
                duration_seconds=outcome.duration_seconds,
                failure=outcome.failure or "",
                truncated=outcome.truncated,
                fence_blocked=blocked,
                can_disable_sandbox=sandbox.allow_unsandboxed_commands,
            )
        )

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, BashOutput):
            return text_block(tool_use_id, str(output))
        parts: list[str] = []
        stdout = output.stdout.rstrip()
        stderr = output.stderr.rstrip()
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(stderr)
        if output.failure:
            parts.append(f"Error: {output.failure}")
        if output.timed_out:
            parts.append("Command timed out")
        if output.interrupted:
            parts.append("Command was interrupted")
        if output.exit_code not in (0, None) and not output.timed_out:
            parts.append(f"Exit code {output.exit_code}")
        if output.truncated:
            parts.append("[Output truncated]")
        if output.fence_blocked:
            parts.append(
                prompts.loop_fence_blocked(
                    list(output.fence_blocked),
                    can_disable=output.can_disable_sandbox,
                )
            )
        if not parts:
            parts.append("(No output)")
        is_error = bool(output.failure) or output.timed_out or output.interrupted
        return text_block(tool_use_id, "\n".join(parts), is_error=is_error)


def _outside_fence(paths: tuple[str, ...], fence: FencePolicy) -> tuple[str, ...]:
    """被拒绝的路径里落在可写边界之外的那些. 边界之内的拒绝是文件系统自己的权限."""
    outside: list[str] = []
    for path in paths:
        inside = False
        for root in fence.all_writable:
            if path == root or path.startswith(root.rstrip("/") + "/"):
                inside = True
                break
        if not inside:
            outside.append(path)
    return tuple(outside)
