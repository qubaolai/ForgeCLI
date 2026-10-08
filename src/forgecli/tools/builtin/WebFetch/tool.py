"""读取公开网页; 本地抓取并转换为 Markdown, 权限仍由本地执行链裁决."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.tool.names import WEB_FETCH_TOOL_NAME
from forgecli.core.tool.tool import (
    CanUseToolFn,
    InterruptBehavior,
    ProgressCallback,
    PromptOptions,
    SearchOrRead,
    Tool,
    ToolResult,
    ToolUseContext,
    ValidationResult,
)
from forgecli.tools.builtin.base import str_of, text_block
from forgecli.tools.builtin.WebFetch.client import WebFetchOutput, fetch_page
from forgecli.tools.builtin.WebFetch.domain_check import normalize_url
from forgecli.tools.builtin.WebFetch.prompt import DESCRIPTION, INPUT_SCHEMA, PROMPT


class WebFetch(Tool):
    name = WEB_FETCH_TOOL_NAME
    aliases = ("web_fetch",)
    search_hint = "读取网页内容"
    input_schema = INPUT_SCHEMA

    def description(self, input: Mapping[str, object]) -> str:
        return DESCRIPTION

    def prompt(self, options: PromptOptions) -> str:
        return PROMPT

    def interrupt_behavior(self) -> InterruptBehavior:
        return InterruptBehavior.CANCEL

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        return True

    def is_concurrency_safe(self, input: Mapping[str, object]) -> bool:
        return True

    def is_search_or_read_command(self, input: Mapping[str, object]) -> SearchOrRead:
        return SearchOrRead(is_read=True)

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        return str_of(input, "url")

    def get_activity_description(self, input: Mapping[str, object]) -> str:
        return f"读取网页: {str_of(input, 'url')}"

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        try:
            normalize_url(str_of(input, "url"))
        except ValueError as exc:
            return ValidationResult(ok=False, message=str(exc))
        return ValidationResult(ok=True)

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        validation = self.validate_input(input, context)
        if not validation.ok:
            raise ValueError(validation.message)
        url = normalize_url(str_of(input, "url"))
        output = asyncio.run(fetch_page(url, abort=context.abort))
        return ToolResult(data=output)

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, WebFetchOutput):
            return text_block(tool_use_id, "Invalid WebFetch output", is_error=True)
        title = f"Title: {output.title}\n" if output.title else ""
        return text_block(
            tool_use_id,
            f"Source: {output.url}\n{title}\n{output.markdown}",
        )
