"""Write (对应 FileWriteTool/FileWriteTool.ts). 已存在的文件要求先读且未被改;
父目录按需建."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionBehavior, PermissionResult
from forgecli.core.tool.file_state import FileState
from forgecli.core.tool.names import TEST_TOOL_NAME
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
from forgecli.tools.builtin.Test.prompt import DESCRIPTION, INPUT_SCHEMA, PROMPT

__all__ = ["TestTool", "TestOutput"]


@dataclass(frozen=True)
class TestOutput:
    output: str


class TestTool(Tool):
    name = TEST_TOOL_NAME
    aliases = ("test",)
    search_hint = "开发者测试工具"
    input_schema = INPUT_SCHEMA
    strict = True
    max_result_size_chars = 100_000

    def description(self, input: Mapping[str, object]) -> str:
        return DESCRIPTION

    def prompt(self, options: PromptOptions) -> str:
        return PROMPT

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        content = str_of(input, "content")
        if content:
            return f"Test input: {content}"
        return "Test Once"

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        return f"{str_of(input, 'content')}"

    def backfill_observable_input(self, input: dict[str, object]) -> None:
        content = input.get("content")
        if isinstance(content, str):
            input["content"] = content

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        output = "接收到了: " + str_of(input, "content")
        return ToolResult(data=TestOutput(output=output))

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, TestOutput):
            return text_block(tool_use_id, str(output))
        return text_block(
            tool_use_id, f"返回给你: {output.output}"
        )
