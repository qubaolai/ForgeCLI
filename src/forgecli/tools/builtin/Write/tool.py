"""Write (对应 FileWriteTool/FileWriteTool.ts). 已存在的文件要求先读且未被改, 权限之后
紧贴落盘再查一次 (file_freshness); 父目录按需建."""

from __future__ import annotations

import os
from collections.abc import Mapping
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
from forgecli.tools.builtin.base import expand_input_path, str_of, text_block
from forgecli.tools.builtin.file_freshness import (
    MODIFIED_SINCE_READ_MESSAGE,
    NOT_READ_MESSAGE,
    Freshness,
    check_freshness,
    read_text_file_if_exists,
    require_fresh,
)
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
        freshness = check_freshness(path, None, context.read_file_state)
        if freshness is Freshness.NOT_READ:
            return ValidationResult(ok=False, message=NOT_READ_MESSAGE, error_code=2)
        if freshness is Freshness.MODIFIED:
            return ValidationResult(
                ok=False, message=MODIFIED_SINCE_READ_MESSAGE, error_code=3
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
        # 备份在关键段之外: 检查失败了多一份没用的备份, 不是错误状态.
        context.track_file_edit(path)
        # 从这里到落盘是关键段 (对应 FileWriteTool.ts 里那段不许 await 的代码): 权限询问
        # 可以等任意长, validate_input 的结论已经过期, 紧贴读盘把同样的前提再查一次.
        current = read_text_file_if_exists(path)
        exists = current is not None
        if current is not None:
            require_fresh(path, current, context.read_file_state)
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
