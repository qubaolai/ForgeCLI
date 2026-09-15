"""一条记忆 (ADR-0033 决策 1 / 6 / 7).

记忆是 ``key -> {value, 来源}`` 的映射, **不是流水**. 静默写入加只增不改等于错误单调
累积: 项目从 ``pytest`` 换到 ``uv run pytest`` 之后, 旧那条永远在, 而模型会看到两条
互相矛盾的记忆并自己挑一条 (决策 6).

来源不是为了审批 —— 决策 2 已经否掉了事前审批. 它是为了排查: "模型为什么突然认为
测试命令是 X" 这个问题只有来源答得上来.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = ["KEY_PATTERN", "MemoryEntry", "MemoryProvenance", "MemoryScope"]

# key 进提示词, 也当 JSON 的键. 收窄到蛇形小写不是洁癖: 一个带换行或方括号的 key 会
# 把渲染出来的记忆块搅成模型读不出结构的一团.
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class MemoryScope(Enum):
    """记忆的生命周期. 判据取自 ADR-0001 补充条款.

    项目事实跟项目走, 输出偏好跟人走 —— 所以它们不能同一个文件.
    """

    PROJECT = "project"
    USER = "user"


@dataclass(frozen=True)
class MemoryProvenance:
    """这条记忆是什么时候, 从哪一轮对话里来的."""

    session_id: str
    turn_id: str
    created_at: str
    # 模型自己说的"我是据什么记下这条的". 一句话, 不是引用.
    derived_from: str = ""


@dataclass(frozen=True)
class MemoryEntry:
    key: str
    value: str
    scope: MemoryScope
    provenance: MemoryProvenance

    def __post_init__(self) -> None:
        if not KEY_PATTERN.match(self.key):
            raise ValueError(f"记忆 key 不合法: {self.key!r}")
        if not self.value.strip():
            raise ValueError("记忆的 value 不能为空")
