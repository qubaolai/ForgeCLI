"""LSP (对应 LSPTool/LSPTool.ts). 只读, 并发安全 (同一服务器上的查询在池里加锁串行).

语言服务器是长期存活的子进程, 由 LspServerPool 按语言各养一个, 项目关闭时关掉. 它不经
CommandExecutor (执行器是"起一个子进程等它结束", 而服务器要活着), 所以围栏管不到它.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionResult
from forgecli.core.tool.names import GREP_TOOL_NAME, LSP_TOOL_NAME
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
from forgecli.security.shell_rule_matching import match_wildcard_pattern
from forgecli.tools.builtin.base import expand_input_path, int_of, str_of, text_block
from forgecli.tools.builtin.LSP.client import LspError
from forgecli.tools.builtin.LSP.operations import (
    needs_position,
    needs_query,
    run_operation,
)
from forgecli.tools.builtin.LSP.pool import LspServerPool
from forgecli.tools.builtin.LSP.prompt import DESCRIPTION, INPUT_SCHEMA, PROMPT
from forgecli.tools.builtin.LSP.servers import (
    language_id_of,
    locate_executable,
    server_for,
)

__all__ = ["LspOutput", "LspTool"]

MAX_DOCUMENT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class LspOutput:
    operation: str
    file_path: str
    text: str
    is_error: bool = False


class LspTool(Tool):
    name = LSP_TOOL_NAME
    aliases = ("lsp",)
    search_hint = "code intelligence: definitions, references, symbols"
    input_schema = INPUT_SCHEMA
    max_result_size_chars = 100_000

    def __init__(self, pool: LspServerPool) -> None:
        self._pool = pool

    def description(self, input: Mapping[str, object]) -> str:
        return DESCRIPTION

    def prompt(self, options: PromptOptions) -> str:
        return PROMPT

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        operation = str_of(input, "operation")
        path = str_of(input, "filePath")
        if operation and path:
            return f"{operation} in {os.path.basename(path)}"
        return "Querying language server"

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        operation = str_of(input, "operation")
        path = str_of(input, "filePath")
        if not operation:
            return None
        return f"{operation} {path}".strip()

    def is_concurrency_safe(self, input: Mapping[str, object]) -> bool:
        return True

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        return True

    def is_search_or_read_command(self, input: Mapping[str, object]) -> SearchOrRead:
        return SearchOrRead(is_search=True, is_read=False)

    def get_path(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "filePath") or None

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        return f"{str_of(input, 'operation')} {str_of(input, 'filePath')}".strip()

    def prepare_permission_matcher(
        self, input: Mapping[str, object]
    ) -> Callable[[str], bool] | None:
        path = str_of(input, "filePath")
        return lambda pattern: match_wildcard_pattern(pattern, path)

    def check_permissions(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> PermissionResult:
        return check_read_permission_for_tool(self, input, context)

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        operation = str_of(input, "operation")
        path = expand_input_path(str_of(input, "filePath"), context.cwd)
        if not os.path.isfile(path):
            return ValidationResult(
                ok=False, message=f"Not a regular file: {path}", error_code=1
            )
        server = server_for(path)
        if server is None:
            return ValidationResult(
                ok=False,
                message=(
                    f"No language server for this file type: {path}. Use "
                    f"{GREP_TOOL_NAME} instead."
                ),
                error_code=2,
            )
        if locate_executable(server, context.environment) is None:
            return ValidationResult(
                ok=False,
                message=(
                    f"Language server {server.executable} is not installed, so {path} "
                    f"cannot be queried. Use {GREP_TOOL_NAME} instead, or ask the "
                    "user to "
                    "install it."
                ),
                error_code=3,
            )
        if needs_position(operation) and (
            int_of(input, "line") is None or int_of(input, "character") is None
        ):
            return ValidationResult(
                ok=False,
                message=f"{operation} requires line and character (both 1-based).",
                error_code=4,
            )
        if needs_query(operation) and not str_of(input, "query").strip():
            return ValidationResult(
                ok=False, message="workspaceSymbol requires query.", error_code=5
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
        operation = str_of(input, "operation")
        path = expand_input_path(str_of(input, "filePath"), context.cwd)
        server = server_for(path)
        if server is None:
            return ToolResult(
                data=LspOutput(
                    operation, path, "No language server for this file type", True
                )
            )
        executable = locate_executable(server, context.environment)
        if executable is None:
            return ToolResult(
                data=LspOutput(
                    operation, path, f"{server.executable} is not installed", True
                )
            )
        try:
            with self._pool.lock_for(server):
                client = self._pool.client_for(server, executable)
                text = _read_text(path)
                client.open_document(path, language_id_of(server, path), text)
                rendered = run_operation(
                    client,
                    operation,
                    path=path,
                    line=int_of(input, "line") or 1,
                    character=int_of(input, "character") or 1,
                    query=str_of(input, "query"),
                    read_text=_read_text_if_text,
                )
        except LspError as exc:
            return ToolResult(data=LspOutput(operation, path, str(exc), True))
        return ToolResult(data=LspOutput(operation, path, rendered))

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, LspOutput):
            return text_block(tool_use_id, str(output))
        if output.is_error:
            return text_block(
                tool_use_id, f"{output.operation} failed: {output.text}", is_error=True
            )
        return text_block(tool_use_id, output.text)


def _read_text(path: str) -> str:
    with open(path, "rb") as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES)
    return raw.decode("utf-8", errors="replace")


def _read_text_if_text(path: str) -> str | None:
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_DOCUMENT_BYTES)
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")
