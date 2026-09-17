"""Write (对应 FileWriteTool/FileWriteTool.ts). 已存在的文件要求先读且未被改;
父目录按需建."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionBehavior, PermissionResult
from forgecli.core.tool.file_state import FileState
from forgecli.core.tool.names import FILE_WRITE_TOOL_NAME
from forgecli.core.tool.tool import (
    CanUseToolFn,
    ProgressCallback,
    PromptOptions,
    Tool,
    ToolResult,
    ToolUseContext,
    ValidationResult,
)
from forgecli.security.filesystem import (
    check_write_permission_for_tool,
    matching_rule_for_input,
)
from forgecli.security.shell_rule_matching import match_wildcard_pattern
from forgecli.tools.builtin.base import expand_input_path, str_of, text_block
from forgecli.tools.builtin.file_ops import create_file, replace_file
from forgecli.tools.builtin.Write.prompt import DESCRIPTION, INPUT_SCHEMA, PROMPT

__all__ = ["FileWriteTool", "WriteOutput"]


@dataclass(frozen=True)
class WriteOutput:
    file_path: str
    created: bool


class FileWriteTool(Tool):
    name = FILE_WRITE_TOOL_NAME
    aliases = ("file_write",)
    search_hint = "create or overwrite files"
    input_schema = INPUT_SCHEMA
    strict = True
    max_result_size_chars = 100_000

    def description(self, input: Mapping[str, object]) -> str:
        return DESCRIPTION

    def prompt(self, options: PromptOptions) -> str:
        return PROMPT

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        path = str_of(input, "file_path")
        if path:
            return f"Writing {os.path.basename(path)}"
        return "Writing file"

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "file_path") or None

    def get_path(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "file_path") or None

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        return f"{str_of(input, 'file_path')}: {str_of(input, 'content')}"

    def is_destructive(self, input: Mapping[str, object]) -> bool:
        path = str_of(input, "file_path")
        return bool(path) and os.path.exists(path)

    def backfill_observable_input(self, input: dict[str, object]) -> None:
        path = input.get("file_path")
        if isinstance(path, str):
            input["file_path"] = expand_input_path(path, os.getcwd())

    def prepare_permission_matcher(
        self, input: Mapping[str, object]
    ) -> Callable[[str], bool] | None:
        path = str_of(input, "file_path")
        return lambda pattern: match_wildcard_pattern(pattern, path)

    def check_permissions(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> PermissionResult:
        return check_write_permission_for_tool(self, input, context)

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        path = expand_input_path(str_of(input, "file_path"), context.cwd)
        if (
            matching_rule_for_input(path, context, "edit", PermissionBehavior.DENY)
            is not None
        ):
            return ValidationResult(
                ok=False,
                message=(
                    "File is in a directory that is denied by your permission "
                    "settings."
                ),
                error_code=1,
            )
        if not os.path.exists(path):
            return ValidationResult(ok=True)
        if os.path.isdir(path):
            return ValidationResult(
                ok=False, message=f"{path} is a directory, not a file.", error_code=4
            )
        state = context.read_file_state.get(path)
        if state is None or state.is_partial_view:
            return ValidationResult(
                ok=False,
                message=(
                    "File has not been read yet. Read it first before writing to it."
                ),
                error_code=2,
            )
        if os.path.getmtime(path) > state.timestamp:
            return ValidationResult(
                ok=False,
                message=(
                    "File has been modified since read, either by the user or by a "
                    "linter. Read it again before attempting to write it."
                ),
                error_code=3,
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
        requested = str_of(input, "file_path")
        path = expand_input_path(requested, context.cwd)
        content = str_of(input, "content")
        exists = os.path.exists(path)
        context.track_file_edit(path)
        if exists:
            replace_file(path, content)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            create_file(path, content)
        context.read_file_state.set(
            path, FileState(content=content, timestamp=os.path.getmtime(path))
        )
        return ToolResult(data=WriteOutput(file_path=requested, created=not exists))

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, WriteOutput):
            return text_block(tool_use_id, str(output))
        if output.created:
            return text_block(
                tool_use_id, f"File created successfully at: {output.file_path}"
            )
        return text_block(
            tool_use_id, f"The file {output.file_path} has been updated successfully."
        )
