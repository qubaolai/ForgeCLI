"""Child task contracts, shared by tools and the runtime."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from forgecli.core.model.usage import UsageRecordDraft
from forgecli.core.tool.tool import ToolUseContext

SUB_AGENT_TOOLS = ("Read", "Glob", "Grep", "LSP", "Bash", "WebSearch", "WebFetch")


class SubAgentLimitError(Exception):
    pass


class SubAgentStatus(StrEnum):
    COMPLETED = "completed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class SubAgentTask:
    description: str
    prompt: str


@dataclass(frozen=True)
class SubAgentIdentity:
    session_id: str
    turn_id: str
    agent_id: str
    parent_tool_call_id: str


@dataclass(frozen=True)
class SubAgentReport:
    identity: SubAgentIdentity
    status: SubAgentStatus
    text: str
    model_calls: int = 0
    tool_calls: int = 0
    usage_drafts: tuple[UsageRecordDraft, ...] = ()
    duration_ms: float = 0
    usage_complete: bool = True


SubAgentRun = Callable[[SubAgentTask, ToolUseContext], SubAgentReport]
