"""Grep (对应 GrepTool/GrepTool.ts). 经 CommandExecutor 起 rg, 与 Bash 同一个执行器
与围栏."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from forgecli.core.execution.fence import FencePolicy
from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionResult
from forgecli.core.tool.names import BASH_TOOL_NAME, GREP_TOOL_NAME
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
from forgecli.security.filesystem import check_read_permission_for_tool
from forgecli.security.shell_rule_matching import match_wildcard_pattern
from forgecli.tools.builtin.base import expand_input_path, int_of, str_of, text_block
from forgecli.tools.builtin.Grep.prompt import DESCRIPTION, INPUT_SCHEMA
from forgecli.tools.builtin.Grep.ripgrep import argv_for, find_ripgrep

__all__ = ["DEFAULT_HEAD_LIMIT", "GrepOutput", "GrepTool"]

DEFAULT_HEAD_LIMIT = 250
_TIMEOUT_SECONDS = 60.0
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024

FenceFactory = Callable[[ToolUseContext], FencePolicy | None]


@dataclass(frozen=True)
class GrepOutput:
    mode: str
    lines: tuple[str, ...]
    total: int
    applied_limit: int | None
    applied_offset: int
    duration_ms: float
    failure: str = ""


class GrepTool(Tool):
    name = GREP_TOOL_NAME
    aliases = ("grep", "search_text")
    search_hint = "search file contents with regex (ripgrep)"
    input_schema = INPUT_SCHEMA
    strict = True
    max_result_size_chars = 20_000

    def __init__(self, executor: CommandExecutor, fence_factory: FenceFactory) -> None:
        self._executor = executor
        self._fence_factory = fence_factory

    def description(self, input: Mapping[str, object]) -> str:
        return "Search for patterns in file contents"

    def prompt(self, options: PromptOptions) -> str:
        return DESCRIPTION

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        pattern = str_of(input, "pattern")
        if pattern:
            return f"Searching for {pattern}"
        return "Searching content"

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "pattern") or None

    def is_concurrency_safe(self, input: Mapping[str, object]) -> bool:
        return True

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        return True

    def is_search_or_read_command(self, input: Mapping[str, object]) -> SearchOrRead:
        return SearchOrRead(is_search=True, is_read=False)

    def get_path(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "path") or None

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        pattern = str_of(input, "pattern")
        path = str_of(input, "path")
        if path:
            return f"{pattern} in {path}"
        return pattern

    def prepare_permission_matcher(
        self, input: Mapping[str, object]
    ) -> Callable[[str], bool] | None:
        pattern = str_of(input, "pattern")
        return lambda rule: match_wildcard_pattern(rule, pattern)

    def check_permissions(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> PermissionResult:
        return check_read_permission_for_tool(self, input, context)

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        if find_ripgrep(context.environment) is None:
            return ValidationResult(
                ok=False,
                message=(
                    "ripgrep (rg) is not installed or not on PATH, so Grep cannot run. "
                    f"Install ripgrep, or fall back to `grep -rn` via {BASH_TOOL_NAME}."
                ),
                error_code=1,
            )
        raw = str_of(input, "path")
        if raw:
            path = expand_input_path(raw, context.cwd)
            if not os.path.exists(path):
                return ValidationResult(
                    ok=False, message=f"Path does not exist: {path}", error_code=2
                )
        return ValidationResult(ok=True)

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        rg = find_ripgrep(context.environment)
        if rg is None:
            raise FileNotFoundError("ripgrep (rg) is not installed")
        path = expand_input_path(str_of(input, "path") or context.cwd, context.cwd)
        mode = str_of(input, "output_mode", "files_with_matches")
        outcome = self._executor.run(
            CommandRequest(
                argv=argv_for(rg, input, path=path),
                cwd=context.cwd,
                environment=context.environment,
                timeout_seconds=_TIMEOUT_SECONDS,
                max_output_bytes=_MAX_OUTPUT_BYTES,
                fence=self._fence_factory(context),
            ),
            context.abort,
        )
        duration = (time.perf_counter() - started) * 1000.0
        if outcome.exit_code not in (0, 1):
            failure = outcome.stderr.strip() or outcome.failure or "ripgrep failed"
            if outcome.timed_out:
                failure = "ripgrep timed out"
            return ToolResult(
                data=GrepOutput(
                    mode=mode,
                    lines=(),
                    total=0,
                    applied_limit=None,
                    applied_offset=0,
                    duration_ms=duration,
                    failure=failure,
                )
            )
        lines = [line for line in outcome.stdout.split("\n") if line]
        if mode == "files_with_matches":
            lines.sort(key=_mtime, reverse=True)
        total = len(lines)
        offset = int_of(input, "offset") or 0
        limit = int_of(input, "head_limit")
        if limit is None:
            limit = DEFAULT_HEAD_LIMIT
        selected = lines[offset:]
        applied_limit: int | None = None
        if limit > 0:
            selected = selected[:limit]
            applied_limit = limit
        return ToolResult(
            data=GrepOutput(
                mode=mode,
                lines=tuple(selected),
                total=total,
                applied_limit=applied_limit,
                applied_offset=offset,
                duration_ms=duration,
            )
        )

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, GrepOutput):
            return text_block(tool_use_id, str(output))
        if output.failure:
            return text_block(tool_use_id, f"Error: {output.failure}", is_error=True)
        if output.total == 0:
            if output.mode == "files_with_matches":
                return text_block(tool_use_id, "No files found")
            return text_block(tool_use_id, "No matches found")
        text = "\n".join(output.lines)
        shown = len(output.lines)
        if output.mode == "files_with_matches":
            text = (
                f"Found {output.total} file{'s' if output.total != 1 else ''}\n{text}"
            )
        remaining = output.total - output.applied_offset - shown
        if remaining > 0:
            text = (
                text + f"\n\n[Showing results {output.applied_offset + 1}-"
                f"{output.applied_offset + shown} of {output.total}. Use offset "
                f"parameter to see more.]"
            )
        return text_block(tool_use_id, text)


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
