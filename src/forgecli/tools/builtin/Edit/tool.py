"""Edit (对应 FileEditTool/FileEditTool.ts).

validate_input 依次: old == new; edit deny 规则; 文件大小; 文件不存在 (old_string 为空
视为新建); 未读过 / 读后被改 (FileStateCache); 定位; 多于一处且没 replace_all.
call 施加, 原子写盘, 更新 FileStateCache, 记文件历史.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionBehavior, PermissionResult
from forgecli.core.tool.file_state import FileState
from forgecli.core.tool.names import FILE_EDIT_TOOL_NAME
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
from forgecli.tools.builtin.Edit.prompt import DESCRIPTION, INPUT_SCHEMA, PROMPT
from forgecli.tools.builtin.Edit.utils import (
    apply_edit,
    find_actual_string,
    snippet_around,
)
from forgecli.tools.builtin.file_ops import create_file, replace_file
from forgecli.tools.builtin.Read.tool import add_line_numbers

__all__ = ["EditOutput", "FileEditTool", "MAX_EDIT_FILE_SIZE", "read_text_file"]

MAX_EDIT_FILE_SIZE = 25 * 1024 * 1024


@dataclass(frozen=True)
class EditOutput:
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool
    snippet: str
    snippet_start_line: int
    note: str = ""


def read_text_file(path: str) -> str:
    with open(path, "rb") as stream:
        raw = stream.read()
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16", errors="replace")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    return text.replace("\r\n", "\n")


def _bool_of(input: Mapping[str, object], key: str) -> bool:
    value = input.get(key)
    return value is True


class FileEditTool(Tool):
    name = FILE_EDIT_TOOL_NAME
    aliases = ("file_edit", "fs_apply_patch")
    search_hint = "modify file contents in place"
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
            return f"Editing {os.path.basename(path)}"
        return "Editing file"

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "file_path") or None

    def get_path(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "file_path") or None

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        return f"{str_of(input, 'file_path')}: {str_of(input, 'new_string')}"

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

    def inputs_equivalent(
        self, first: Mapping[str, object], second: Mapping[str, object]
    ) -> bool:
        keys = ("file_path", "old_string", "new_string", "replace_all")
        return all(first.get(key) == second.get(key) for key in keys)

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        path = expand_input_path(str_of(input, "file_path"), context.cwd)
        old_string = str_of(input, "old_string")
        new_string = str_of(input, "new_string")
        replace_all = _bool_of(input, "replace_all")
        if old_string == new_string:
            return ValidationResult(
                ok=False,
                message=(
                    "No changes to make: old_string and new_string are exactly the "
                    "same."
                ),
                error_code=1,
            )
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
                error_code=2,
            )
        if os.path.exists(path) and os.path.getsize(path) > MAX_EDIT_FILE_SIZE:
            return ValidationResult(
                ok=False,
                message=(
                    f"File is too large to edit ({os.path.getsize(path)} bytes). "
                    "Maximum "
                    f"editable file size is {MAX_EDIT_FILE_SIZE} bytes."
                ),
                error_code=10,
            )
        if not os.path.exists(path):
            if old_string == "":
                return ValidationResult(ok=True)
            return ValidationResult(
                ok=False,
                message=(
                    "File does not exist. Note that relative paths are resolved from "
                    f"the current working directory {context.cwd}."
                ),
                error_code=4,
            )
        if os.path.isdir(path):
            return ValidationResult(
                ok=False, message=f"{path} is a directory, not a file.", error_code=4
            )
        content = read_text_file(path)
        if old_string == "":
            if content.strip() != "":
                return ValidationResult(
                    ok=False,
                    message="Cannot create new file - file already exists.",
                    error_code=3,
                )
            return ValidationResult(ok=True)
        stale = _stale_check(path, content, context)
        if stale is not None:
            return stale
        located = find_actual_string(content, old_string, new_string)
        if located.actual is None:
            message = f"String to replace not found in file.\nString: {old_string}"
            if located.hint:
                message = message + "\n" + located.hint
            return ValidationResult(ok=False, message=message, error_code=8)
        matches = content.count(located.actual)
        if matches > 1 and not replace_all:
            return ValidationResult(
                ok=False,
                message=(
                    f"Found {matches} matches of the string to replace, but "
                    "replace_all "
                    "is false. To replace all occurrences, set replace_all to true. To "
                    "replace only one occurrence, please provide more context to "
                    f"uniquely identify the instance.\nString: {old_string}"
                ),
                error_code=9,
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
        old_string = str_of(input, "old_string")
        new_string = str_of(input, "new_string")
        replace_all = _bool_of(input, "replace_all")
        exists = os.path.exists(path)
        content = read_text_file(path) if exists else ""
        note = ""
        actual = old_string
        written_new = new_string
        if old_string != "":
            located = find_actual_string(content, old_string, new_string)
            if located.actual is None:
                raise ValueError(
                    f"String to replace not found in file.\nString: {old_string}"
                )
            actual = located.actual
            note = located.note
            if located.aligned_new is not None:
                written_new = located.aligned_new
        updated = apply_edit(content, actual, written_new, replace_all=replace_all)
        context.track_file_edit(path)
        if exists:
            replace_file(path, updated)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            create_file(path, updated)
        context.read_file_state.set(
            path, FileState(content=updated, timestamp=os.path.getmtime(path))
        )
        snippet, start = snippet_around(updated, actual, written_new)
        return ToolResult(
            data=EditOutput(
                file_path=requested,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
                snippet=snippet,
                snippet_start_line=start,
                note=note,
            )
        )

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, EditOutput):
            return text_block(tool_use_id, str(output))
        if output.old_string == "":
            return text_block(
                tool_use_id,
                f"The file {output.file_path} has been created successfully.",
            )
        text = (
            f"The file {output.file_path} has been updated. Here's the result of "
            f"running `cat -n` on a snippet of the edited file:\n"
            + add_line_numbers(output.snippet + "\n", output.snippet_start_line)
        )
        if output.note:
            text = text + f"\n\nNote: {output.note}."
        return text_block(tool_use_id, text)


def _stale_check(
    path: str, content: str, context: ToolUseContext
) -> ValidationResult | None:
    state = context.read_file_state.get(path)
    if state is None or state.is_partial_view:
        return ValidationResult(
            ok=False,
            message="File has not been read yet. Read it first before writing to it.",
            error_code=6,
        )
    if os.path.getmtime(path) > state.timestamp:
        if state.is_full_read and content == state.content:
            return None
        return ValidationResult(
            ok=False,
            message=(
                "File has been modified since read, either by the user or by a linter. "
                "Read it again before attempting to write it."
            ),
            error_code=7,
        )
    return None
