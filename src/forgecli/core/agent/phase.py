"""循环处在哪一步 (ADR-0049 决策 1, ADR-0051 收成三个).

一次 `run()` 跑到底之后只剩三种: 没开始, 在跑, 结束. 原先的两个"等待"态是握手留下的
(等工具结果, 等回答确认), 握手删了它们就没了.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["LoopPhase"]


class LoopPhase(Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    FINISHED = "finished"
