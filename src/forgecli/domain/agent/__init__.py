"""Agent 的领域词汇: 动作, 状态, 停止原因, 运行事件, hook 返回值。

运行事件的词汇在 run_events (ADR-0016), 不在这里转手再导出: 它的名字比循环动作多一个
数量级, 全铺进包门面会让"这个包是干什么的"读不出来。按模块路径直接 import。
"""

from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopAction,
    LoopDecision,
    LoopObservation,
    LoopStop,
    ObservationDisposition,
    ObservationSource,
    ToolRequest,
    ToolRequestAction,
)
from forgecli.domain.agent.hooks import HookResult
from forgecli.domain.agent.state import (
    ContextPackage,
    LoopBudgets,
    LoopInput,
    LoopState,
)
from forgecli.domain.agent.stop import LoopStopReason, StopClassification

__all__ = [
    "AnswerAction",
    "ContextPackage",
    "HookResult",
    "LoopAction",
    "LoopBudgets",
    "LoopDecision",
    "LoopInput",
    "LoopObservation",
    "LoopState",
    "LoopStop",
    "LoopStopReason",
    "ObservationDisposition",
    "ObservationSource",
    "StopClassification",
    "ToolRequest",
    "ToolRequestAction",
]
