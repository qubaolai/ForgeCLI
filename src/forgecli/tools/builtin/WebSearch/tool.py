"""联网搜索: 通过 Parallel MCP 获取带来源的搜索摘录."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

from forgecli.common import __version__
from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.tool.names import WEB_SEARCH_TOOL_NAME
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
from forgecli.tools.builtin.base import int_of, str_of, text_block
from forgecli.tools.builtin.WebSearch.client import WebSearchOutput, call_mcp
from forgecli.tools.builtin.WebSearch.prompt import DESCRIPTION, PROMPT, input_schema


class WebSearch(Tool):
    name = WEB_SEARCH_TOOL_NAME
    aliases = ("web_search",)
    search_hint = "联网搜索"

    def __init__(self, *, api_key: str = "") -> None:
        self._api_key = api_key.strip()
        self.input_schema = input_schema(authenticated=bool(self._api_key))

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
        return SearchOrRead(is_search=True)

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        return str_of(input, "query")

    def get_activity_description(self, input: Mapping[str, object]) -> str:
        return f"Search keywords: {str_of(input, 'query')}"

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        if not str_of(input, "query").strip():
            return ValidationResult(ok=False, message="query must not be blank")
        if "num_results" in input:
            count = int_of(input, "num_results")
            if not self._api_key:
                return ValidationResult(
                    ok=False, message="num_results requires PARALLEL_API_KEY"
                )
            if count is None or not 1 <= count <= 10:
                return ValidationResult(
                    ok=False, message="num_results must be an integer from 1 to 10"
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
        validation = self.validate_input(input, context)
        if not validation.ok:
            raise ValueError(validation.message)
        query = str_of(input, "query").strip()
        headers = {"User-Agent": f"forge/{__version__}"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
            headers["x-parallel-search-config"] = json.dumps(
                {
                    "mode": "fast",
                    "advanced_settings": {
                        "max_results": int_of(input, "num_results") or 5
                    },
                }
            )
        output = asyncio.run(
            call_mcp(
                {"objective": query, "search_queries": [query]},
                headers=headers,
                abort=context.abort,
            )
        )
        return ToolResult(data=output)

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, WebSearchOutput):
            return text_block(tool_use_id, "Invalid WebSearch output", is_error=True)
        return text_block(tool_use_id, output.text, is_error=output.is_error)
