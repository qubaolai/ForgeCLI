from collections.abc import Mapping

from forgecli.core.agent.sub_agent import (
    SubAgentLimitError,
    SubAgentReport,
    SubAgentRun,
    SubAgentStatus,
    SubAgentTask,
)
from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.tool.tool import (
    CanUseToolFn,
    ProgressCallback,
    PromptOptions,
    Tool,
    ToolResult,
    ToolUseContext,
    ValidationResult,
)
from forgecli.tools.builtin.Agent.prompt import DESCRIPTION, INPUT_SCHEMA


class AgentTool(Tool):
    name = "Agent"
    input_schema = INPUT_SCHEMA
    max_result_size_chars = 20000

    def __init__(self, run: SubAgentRun) -> None:
        self._run = run

    def description(self, input: Mapping[str, object]) -> str:
        return str(input.get("description", "Explore code"))

    def prompt(self, options: PromptOptions) -> str:
        return DESCRIPTION

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        return True

    def is_concurrency_safe(self, input: Mapping[str, object]) -> bool:
        return True

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        return ValidationResult(
            ok=all(str(input.get(k, "")).strip() for k in ("description", "prompt")),
            message="description and prompt must not be empty",
        )

    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        try:
            report = self._run(
                SubAgentTask(str(input["description"]), str(input["prompt"])), context
            )
            return ToolResult(data=report)
        except SubAgentLimitError as error:
            return ToolResult(data=str(error))

    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        if isinstance(output, SubAgentReport):
            return ToolResultBlock(
                tool_call_id=tool_use_id,
                content=output.text,
                is_error=output.status is SubAgentStatus.FAILED,
            )
        return ToolResultBlock(
            tool_call_id=tool_use_id, content=str(output), is_error=True
        )
