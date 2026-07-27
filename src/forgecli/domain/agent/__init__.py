"""Agent 循环的领域词汇: 动作, 状态, 停止原因。"""

from forgecli.domain.agent.actions import (
    AnswerAction,
    ApprovalRequest,
    ApprovalRequestAction,
    AskUserAction,
    CompactionRequestAction,
    LoopAction,
    LoopDecision,
    LoopObservation,
    LoopStepResult,
    LoopStop,
    ToolRequest,
    ToolRequestAction,
)
from forgecli.domain.agent.state import (
    ContextPackage,
    LoopBudgets,
    LoopInput,
    LoopState,
    ModePolicy,
)
from forgecli.domain.agent.stop import LoopStopReason, StopClassification

__all__ = [
    "AnswerAction",
    "ApprovalRequest",
    "ApprovalRequestAction",
    "AskUserAction",
    "CompactionRequestAction",
    "ContextPackage",
    "LoopAction",
    "LoopBudgets",
    "LoopDecision",
    "LoopInput",
    "LoopObservation",
    "LoopState",
    "LoopStep",
    "LoopStepResult",
    "LoopStop",
    "LoopStopReason",
    "ModePolicy",
    "StopClassification",
    "ToolRequest",
    "ToolRequestAction",
]
