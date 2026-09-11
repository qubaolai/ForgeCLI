"""循环在两次被驱动之间处在哪一步 (ADR-0049 决策 1).

原先靠 `_started` / `_finished` / `_dispatched is None` 三个字段拼: 最后那个有四种
可能 (还没派过工具, 工具刚跑完, 回答已产出等确认, 本轮已结束), 要再看另外两个才分得
清, 而任何一种组合都不会报错. 一个枚举把这件事说死.

只有这四个. 等计划评审是一个停止原因 (`LoopStopReason.WAIT_PLAN_REVIEW`), 不是循环的
状态: 循环停了, 等的是下一轮.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["LoopPhase"]


class LoopPhase(Enum):
    NOT_STARTED = "not_started"
    # 派出去一个工具, 等驱动方回填结果.
    AWAITING_TOOL_RESULT = "awaiting_tool_result"
    # 回答已产出, 等驱动方确认送达.
    AWAITING_ANSWER_ACK = "awaiting_answer_ack"
    FINISHED = "finished"
