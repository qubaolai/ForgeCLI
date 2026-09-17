"""memory_write / memory_forget (ADR-0033). 写哪由 scope 算出, 模型指定不了, 缺省
allow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.tool.names import MEMORY_FORGET_TOOL_NAME, MEMORY_WRITE_TOOL_NAME
from forgecli.core.tool.tool import (
    CanUseToolFn,
    ProgressCallback,
    PromptOptions,
    Tool,
    ToolResult,
    ToolUseContext,
    ValidationResult,
)
from forgecli.memory.entry import MemoryScope
from forgecli.memory.service import MemoryService
from forgecli.prompt import prompts
from forgecli.tools.builtin.base import str_of, text_block
from forgecli.tools.builtin.memory.notices import rejection_text
from forgecli.tools.builtin.memory.prompt import (
    FORGET_PROMPT,
    SCOPE_DESCRIPTION,
    WRITE_PROMPT,
)

__all__ = ["MemoryForgetTool", "MemoryOutput", "MemoryWriteTool"]

_SCOPE_VALUES = tuple(scope.value for scope in MemoryScope)


@dataclass(frozen=True)
class MemoryOutput:
    text: str
    is_error: bool = False


def _schema(
    properties: Mapping[str, object], required: tuple[str, ...]
) -> Mapping[str, object]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


class _MemoryTool(Tool):
    max_result_size_chars = 100_000

    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        if str_of(input, "scope") not in _SCOPE_VALUES:
            return ValidationResult(ok=False, message=SCOPE_DESCRIPTION, error_code=1)
        return ValidationResult(ok=True)

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "key") or None

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, MemoryOutput):
            return text_block(tool_use_id, str(output))
        return text_block(tool_use_id, output.text, is_error=output.is_error)


class MemoryWriteTool(_MemoryTool):
    name = MEMORY_WRITE_TOOL_NAME
    search_hint = "remember a fact for later sessions"
    input_schema = _schema(
        {
            "scope": {
                "type": "string",
                "enum": list(_SCOPE_VALUES),
                "description": SCOPE_DESCRIPTION,
            },
            "key": {
                "type": "string",
                "description": "short snake_case identifier, e.g. test_command",
            },
            "value": {
                "type": "string",
                "description": "the fact itself, one or two sentences",
            },
            "derived_from": {
                "type": "string",
                "description": "what you based this on, one sentence",
            },
        },
        ("scope", "key", "value"),
    )

    def description(self, input: Mapping[str, object]) -> str:
        return "Remember a fact"

    def prompt(self, options: PromptOptions) -> str:
        return WRITE_PROMPT

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        return f"Remembering {str_of(input, 'key')}"

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        key = str_of(input, "key")
        written = self._memory.remember(
            MemoryScope(str_of(input, "scope")),
            key,
            str_of(input, "value"),
            derived_from=str_of(input, "derived_from"),
        )
        if written.rejection is not None:
            return ToolResult(
                data=MemoryOutput(rejection_text(written.rejection), True)
            )
        if written.replaced:
            body = prompts.memory_replaced(key, written.replaced)
        else:
            body = prompts.memory_written(key)
        return ToolResult(data=MemoryOutput(body))


class MemoryForgetTool(_MemoryTool):
    name = MEMORY_FORGET_TOOL_NAME
    search_hint = "delete a memory"
    input_schema = _schema(
        {
            "scope": {
                "type": "string",
                "enum": list(_SCOPE_VALUES),
                "description": SCOPE_DESCRIPTION,
            },
            "key": {"type": "string"},
        },
        ("scope", "key"),
    )

    def description(self, input: Mapping[str, object]) -> str:
        return "Forget a memory"

    def prompt(self, options: PromptOptions) -> str:
        return FORGET_PROMPT

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        return f"Forgetting {str_of(input, 'key')}"

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        key = str_of(input, "key")
        if self._memory.forget(MemoryScope(str_of(input, "scope")), key):
            return ToolResult(data=MemoryOutput(prompts.memory_forgotten(key)))
        return ToolResult(data=MemoryOutput(prompts.memory_not_found(key)))
