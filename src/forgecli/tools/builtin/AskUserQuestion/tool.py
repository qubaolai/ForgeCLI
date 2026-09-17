"""AskUserQuestion (对应 AskUserQuestionTool). 等待发生在 call 内部, 回答就是这次调用
的结果.

requires_user_interaction 为真: 即使在 bypass 下, 它的 ask 也要摆卡片 —— 卡片本身就是
这个工具要做的事. 没人回答时不停下循环 (ADR-0043 决策 8): 模型得自己拿主意.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.tool.names import ASK_USER_QUESTION_TOOL_NAME
from forgecli.core.tool.tool import (
    CanUseToolFn,
    ProgressCallback,
    PromptOptions,
    Tool,
    ToolResult,
    ToolUseContext,
    ValidationResult,
)
from forgecli.tools.builtin.AskUserQuestion.prompt import DESCRIPTION, PROMPT
from forgecli.tools.builtin.AskUserQuestion.prompting import (
    MAX_OPTIONS,
    answer_text,
    build_prompt,
)
from forgecli.tools.builtin.base import str_of, text_block

__all__ = ["AskUserQuestionTool", "QuestionOutput"]

_INPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "minLength": 1, "maxLength": 500},
        "description": {"type": "string", "maxLength": 1000},
        "selection_mode": {"type": "string", "enum": ["single", "multiple"]},
        "recommended_option_id": {"type": "string"},
        "options": {
            "type": "array",
            "maxItems": MAX_OPTIONS,
            "items": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "minLength": 1, "pattern": r"\S"},
                    "label": {"type": "string", "minLength": 1, "pattern": r"\S"},
                    "detail": {"type": "string", "minLength": 1, "pattern": r"\S"},
                },
                "required": ["value", "label", "detail"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class QuestionOutput:
    status: str  # answered / skipped / unanswered
    text: str


class AskUserQuestionTool(Tool):
    name = ASK_USER_QUESTION_TOOL_NAME
    aliases = ("ask_user",)
    search_hint = "ask the user a question"
    input_schema = _INPUT_SCHEMA
    max_result_size_chars = 100_000

    def description(self, input: Mapping[str, object]) -> str:
        return DESCRIPTION

    def prompt(self, options: PromptOptions) -> str:
        return PROMPT

    def requires_user_interaction(self) -> bool:
        return True

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        return True

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        return "Asking the user"

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        return str_of(input, "question") or None

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        return str_of(input, "question")

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        try:
            build_prompt("validation", input)
        except ValueError as exc:
            return ValidationResult(ok=False, message=str(exc), error_code=1)
        return ValidationResult(ok=True)

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        prompt_id = f"question_{uuid.uuid4().hex[:12]}"
        answer = context.prompts.ask(build_prompt(prompt_id, input))
        if not answer.resolved:
            note = answer.note or "no answer was received"
            return ToolResult(
                data=QuestionOutput(
                    status="unanswered",
                    text=(
                        f"{note}. Continue on your own judgement and state the "
                        "assumptions you made in your final answer."
                    ),
                )
            )
        status = "skipped" if answer.skipped else "answered"
        return ToolResult(
            data=QuestionOutput(status=status, text=answer_text(input, answer))
        )

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if not isinstance(output, QuestionOutput):
            return text_block(tool_use_id, str(output))
        return text_block(
            tool_use_id, output.text, is_error=output.status == "unanswered"
        )
