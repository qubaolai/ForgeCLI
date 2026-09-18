"""Glob (对应 GlobTool/GlobTool.ts). 只读, 并发安全; 尊重 .gitignore, 结果按 mtime
从新到旧."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionResult
from forgecli.core.tool.names import GLOB_TOOL_NAME
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
from forgecli.security.filesystem import check_read_permission_for_tool
from forgecli.tools.builtin.base import expand_input_path, str_of, text_block
from forgecli.tools.builtin.Glob.prompt import DESCRIPTION, INPUT_SCHEMA
from forgecli.workspace.filesystem_view import OsFileSystemView

__all__ = ["GlobOutput", "GlobTool", "MAX_RESULTS"]

MAX_RESULTS = 100


@dataclass(frozen=True)
class GlobOutput:
    filenames: tuple[str, ...]
    duration_ms: float
    num_files: int
    truncated: bool


class GlobTool(Tool):
    name = GLOB_TOOL_NAME
    aliases = ("glob",)
    search_hint = "find files by name pattern or wildcard"
    input_schema = INPUT_SCHEMA
    max_result_size_chars = 100_000

    def __init__(self, filesystem: OsFileSystemView | None = None) -> None:
        self._filesystem = filesystem or OsFileSystemView()

    def description(self, input: Mapping[str, object]) -> str:
        return "Search for files matching a pattern"

    def prompt(self, options: PromptOptions) -> str:
        return DESCRIPTION

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        pattern = str_of(input, "pattern")
        if pattern:
            return f"Searching for {pattern}"
        return "Searching files"

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
        return str_of(input, "pattern")

    def check_permissions(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> PermissionResult:
        return check_read_permission_for_tool(self, input, context)

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        raw = str_of(input, "path")
        if not raw:
            return ValidationResult(ok=True)
        if raw in ("undefined", "null"):
            return ValidationResult(
                ok=False,
                message=(
                    f'The "path" parameter was set to "{raw}". Omit the parameter to '
                    "search the current working directory."
                ),
                error_code=1,
            )
        path = expand_input_path(raw, context.cwd)
        if not os.path.exists(path):
            return ValidationResult(
                ok=False, message=f"Path does not exist: {path}", error_code=2
            )
        if not os.path.isdir(path):
            return ValidationResult(
                ok=False, message=f"Path is not a directory: {path}", error_code=3
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
        pattern = str_of(input, "pattern")
        root = expand_input_path(str_of(input, "path") or context.cwd, context.cwd)
        matches = self._filesystem.expand_glob(
            pattern, root=root, max_results=MAX_RESULTS + 1, skip_ignored=True
        )
        files: list[str] = []
        for candidate in matches:
            absolute = candidate
            if not os.path.isabs(candidate):
                absolute = os.path.join(root, candidate)
            if os.path.isfile(absolute):
                files.append(absolute)
        truncated = len(files) > MAX_RESULTS
        files = files[:MAX_RESULTS]
        files.sort(key=_mtime, reverse=True)
        return ToolResult(
            data=GlobOutput(
                filenames=tuple(files),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                num_files=len(files),
                truncated=truncated,
            )
        )

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, GlobOutput):
            return text_block(tool_use_id, str(output))
        if not output.filenames:
            return text_block(tool_use_id, "No files found")
        text = "\n".join(output.filenames)
        if output.truncated:
            text = (
                text
                + "\n(Results are truncated. Consider using a more specific path or "
                "pattern.)"
            )
        return text_block(tool_use_id, text)


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
