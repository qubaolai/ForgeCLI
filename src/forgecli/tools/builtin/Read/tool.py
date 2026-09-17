"""Read (对应 FileReadTool/FileReadTool.ts).

只读, 并发安全, 永不落盘 (落盘之后让模型再用 Read 读一遍是循环). 读完写
FileStateCache, Edit / Write 据此要求"先读再改".
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionBehavior, PermissionResult
from forgecli.core.tool.file_state import FileState
from forgecli.core.tool.names import FILE_READ_TOOL_NAME
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
from forgecli.security.filesystem import (
    check_read_permission_for_tool,
    matching_rule_for_input,
)
from forgecli.security.shell_rule_matching import match_wildcard_pattern
from forgecli.tools.builtin.base import expand_input_path, int_of, str_of, text_block
from forgecli.tools.builtin.Read.prompt import (
    DESCRIPTION,
    INPUT_SCHEMA,
    MAX_LINES_TO_READ,
    prompt,
)

__all__ = ["FileReadTool", "ReadOutput", "add_line_numbers"]

# 没给 limit 时的文件总大小上限 (对应 maxSizeBytes). 超了让模型用 offset / limit.
MAX_SIZE_BYTES = 256 * 1024
# 单行上限: 超过的截断 (压缩过的 JS 一行几十万字符, 全塞进上下文没有意义).
MAX_LINE_LENGTH = 2000

_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".tif",
        ".tiff",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".o",
        ".a",
        ".class",
        ".jar",
        ".pyc",
        ".pyo",
        ".wasm",
        ".mp3",
        ".mp4",
        ".mov",
        ".avi",
        ".wav",
        ".flac",
        ".ogg",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".sqlite",
        ".db",
    }
)
_BLOCKED_DEVICE_PATHS = frozenset(
    {
        "/dev/stdin",
        "/dev/zero",
        "/dev/random",
        "/dev/urandom",
        "/dev/tty",
        "/dev/console",
    }
)


@dataclass(frozen=True)
class ReadOutput:
    file_path: str
    content: str
    num_lines: int
    start_line: int
    total_lines: int


def add_line_numbers(content: str, start_line: int) -> str:
    """cat -n 的格式: 六位右对齐的行号加一个箭头."""
    if not content:
        return ""
    lines = content.split("\n")
    numbered: list[str] = []
    for index, line in enumerate(lines):
        if index == len(lines) - 1 and line == "":
            continue
        numbered.append(f"{start_line + index:6d}→{line}")
    return "\n".join(numbered)


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    if size >= 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size}B"


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


class FileReadTool(Tool):
    name = FILE_READ_TOOL_NAME
    aliases = ("file_read", "fs_read")
    search_hint = "read files"
    input_schema = INPUT_SCHEMA
    strict = True
    max_result_size_chars = math.inf  # type: ignore[assignment]

    def __init__(self, read_bytes: Callable[[str], bytes] | None = None) -> None:
        self._read_bytes = read_bytes or _read_bytes

    def description(self, input: Mapping[str, object]) -> str:
        return DESCRIPTION

    def prompt(self, options: PromptOptions) -> str:
        return prompt(_format_size(MAX_SIZE_BYTES))

    def user_facing_name(self, input: Mapping[str, object]) -> str:
        return self.name

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        path = str_of(input, "file_path")
        if not path:
            return None
        offset = int_of(input, "offset")
        limit = int_of(input, "limit")
        if offset is not None and limit is not None:
            return f"{path} (lines {offset}-{offset + limit - 1})"
        if offset is not None:
            return f"{path} (from line {offset})"
        return path

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        summary = self.get_tool_use_summary(input)
        if summary:
            return f"Reading {summary}"
        return "Reading file"

    def is_concurrency_safe(self, input: Mapping[str, object]) -> bool:
        return True

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        return True

    def is_search_or_read_command(self, input: Mapping[str, object]) -> SearchOrRead:
        return SearchOrRead(is_search=False, is_read=True)

    def get_path(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "file_path") or None

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        return str_of(input, "file_path")

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
        return check_read_permission_for_tool(self, input, context)

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        path = expand_input_path(str_of(input, "file_path"), context.cwd)
        if (
            matching_rule_for_input(path, context, "read", PermissionBehavior.DENY)
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
        extension = os.path.splitext(path)[1].lower()
        if extension in _BINARY_EXTENSIONS:
            return ValidationResult(
                ok=False,
                message=(
                    f"This tool cannot read binary files. The file appears to be a "
                    f"binary {extension} file. Please use appropriate tools for binary "
                    "file analysis."
                ),
                error_code=4,
            )
        if path in _BLOCKED_DEVICE_PATHS:
            return ValidationResult(
                ok=False,
                message=(
                    f"Cannot read '{path}': this device file would block or produce "
                    "infinite output."
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
        offset = int_of(input, "offset")
        limit = int_of(input, "limit")
        if offset is None:
            offset = 1
        if not os.path.exists(path):
            raise FileNotFoundError(f"File does not exist: {path}")
        if os.path.isdir(path):
            raise IsADirectoryError(
                f"{path} is a directory. Use an ls command via Bash to list it."
            )
        size = os.path.getsize(path)
        if limit is None and size > MAX_SIZE_BYTES:
            raise ValueError(
                f"File content ({_format_size(size)}) exceeds maximum allowed size "
                f"({_format_size(MAX_SIZE_BYTES)}). Please use offset and limit "
                "parameters to read specific portions of the file, or search for "
                "specific content instead of reading the whole file."
            )
        raw = self._read_bytes(path)
        text = _decode(raw).replace("\r\n", "\n")
        all_lines = text.split("\n")
        if all_lines and all_lines[-1] == "":
            all_lines.pop()
        total = len(all_lines)
        start_index = max(offset - 1, 0)
        count = limit if limit is not None else MAX_LINES_TO_READ
        selected = all_lines[start_index : start_index + count]
        truncated: list[str] = []
        for line in selected:
            if len(line) > MAX_LINE_LENGTH:
                truncated.append(line[:MAX_LINE_LENGTH] + "… [line truncated]")
            else:
                truncated.append(line)
        content = "\n".join(truncated)
        if truncated:
            content = content + "\n"
        context.read_file_state.set(
            path,
            FileState(
                content=content,
                timestamp=os.path.getmtime(path),
                offset=None if input.get("offset") is None else offset,
                limit=limit,
            ),
        )
        return ToolResult(
            data=ReadOutput(
                file_path=requested,
                content=content,
                num_lines=len(truncated),
                start_line=offset,
                total_lines=total,
            )
        )

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, ReadOutput):
            return text_block(tool_use_id, str(output))
        if not output.content.strip():
            return text_block(
                tool_use_id,
                "<system-reminder>Warning: the file exists but the contents are empty."
                "</system-reminder>",
            )
        numbered = add_line_numbers(output.content, output.start_line)
        if output.start_line + output.num_lines - 1 < output.total_lines:
            numbered = (
                numbered
                + "\n\n(File has more lines. Use 'offset' parameter to read beyond "
                f"line "
                f"{output.start_line + output.num_lines - 1}; total lines: "
                f"{output.total_lines})"
            )
        return text_block(tool_use_id, numbered)


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as stream:
        return stream.read()
