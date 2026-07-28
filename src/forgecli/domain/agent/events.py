"""循环观察事件的词汇（ADR-0010 §7.2）。

只读通知的**形状**属于领域: 换掉事件总线的实现 (进程内分发 / 异步队列 / 落盘) 不改变
"循环会发生哪些事、每件事带什么摘要"。总线与订阅端口是编排设施, 留在 application。

约束（§7.2）：LoopEvent 不允许改变控制流；需要改变循环方向的能力必须实现 LoopHook。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

__all__ = ["LoopEvent", "LoopEventKind"]


class LoopEventKind(Enum):
    """循环观察事件类型（§7.2）。值即落盘 / 序列化字符串。"""

    LOOP_STARTED = "loop_started"
    STEP_STARTED = "step_started"
    MODEL_REQUESTED = "model_requested"
    MODEL_COMPLETED = "model_completed"
    ACTION_REQUESTED = "action_requested"
    ACTION_COMPLETED = "action_completed"
    LOOP_STOPPED = "loop_stopped"


@dataclass(frozen=True)
class LoopEvent:
    """只读循环通知。payload 只含安全摘要，不含 raw CoT 或凭证。"""

    kind: LoopEventKind
    turn_id: str
    payload: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("LoopEvent.turn_id 不能为空")
