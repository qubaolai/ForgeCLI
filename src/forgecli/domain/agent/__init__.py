"""Agent 循环的领域词汇: 动作, 状态, 停止原因, 观察事件, hook 返回值。"""

from forgecli.domain.agent.actions import (
    AnswerAction,
    ApprovalRequest,
    ApprovalRequestAction,
    AskUserAction,
    CompactionRequestAction,
    LoopAction,
    LoopDecision,
    LoopObservation,
    LoopStop,
    ToolRequest,
    ToolRequestAction,
)
from forgecli.domain.agent.events import LoopEvent, LoopEventKind
from forgecli.domain.agent.hooks import HookResult
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
    "HookResult",
    "LoopAction",
    "LoopBudgets",
    "LoopDecision",
    "LoopEvent",
    "LoopEventKind",
    "LoopInput",
    "LoopObservation",
    "LoopState",
    "LoopStop",
    "LoopStopReason",
    "ModePolicy",
    "StopClassification",
    "ToolRequest",
    "ToolRequestAction",
]
