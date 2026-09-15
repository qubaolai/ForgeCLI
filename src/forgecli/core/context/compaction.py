"""一次压缩留下的记录 (ADR-0032 决策 8).

循环产出草稿, **驱动方落盘** —— 与 ``UsageRecordDraft`` 完全同构. 这条分工来自
ADR-0010: 循环可以调模型 (它本来就持有 ``LlmGateway``), 但不能写事件.

草稿里没有 event_id 区间. ``_rebuild_transcript`` 是**按位置**重放的 —— 读到最后一条
``CONTEXT_COMPACTED`` 就把已累积的 transcript 丢掉, 从摘要接着往下走, 事件在日志里的
位置本身就定义了它覆盖的范围. 再存一份起止 id 没有消费方, 那正是 ADR-0028 规则 C 说的
摆着不生效的字段.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["CompactionDraft", "CompactionLevel"]


class CompactionLevel(Enum):
    """压缩的种类. 值即落盘字符串.

    只剩一种. 原先还有一级 ``DOWNGRADE`` (把工具结果正文换成 artifact 引用), 它随
    ADR-0041 决策 6 消失 —— 正文现在**本来就不进窗口**, 没有可降的东西了. 留一个只有
    一个成员的枚举是刻意的: 落盘字符串已经在历史事件里, 换成布尔要动重放.
    """

    # 用模型把一批被淘汰的消息换成一段交接说明. 不可复现, 所以正文进事件 payload.
    SUMMARY = "summary"


@dataclass(frozen=True)
class CompactionDraft:
    """一次窗口淘汰的记录. 摘要正文进 payload, 不留引用.

    这一条与 ADR-0022 计划正文"只记引用"的取舍相反, 理由是**可复现性**: 计划正文的
    真相源是磁盘上的文件, 随时读得回来; 而摘要是模型一次性产出的, 引用的目标一旦被
    回收就永远重建不出来, 那条会话的 ``/resume`` 从此少一段历史.

    ``summary`` 允许为空: 没接网关, 或者模型回了一段空白时, 淘汰照样发生 —— 窗口撞了
    高水位, 不淘汰这一轮就发不出去. 那种情况下被丢掉的用户原话仍然逐字留在窗口首条
    (ADR-0041 决策 5), 只是没有交接说明. 原先这里强制 SUMMARY 级必须带正文, 那是在
    还有一个 DOWNGRADE 级可退的时候; 现在只剩这一级, 强制它等于让淘汰在最需要的时刻
    抛异常.
    """

    level: CompactionLevel
    tokens_before: int
    tokens_after: int
    # 这次淘汰丢掉了几条消息.
    messages_replaced: int = 0
    summary: str = ""
    provider: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if self.tokens_before < 0 or self.tokens_after < 0:
            raise ValueError("CompactionDraft 的 token 数不能为负")
        if self.messages_replaced < 0:
            raise ValueError("CompactionDraft.messages_replaced 不能为负")

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    def to_payload(self) -> dict[str, object]:
        return {
            "level": self.level.value,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "tokens_saved": self.tokens_saved,
            "messages_replaced": self.messages_replaced,
            "summary": self.summary,
            "provider": self.provider,
            "model": self.model,
        }
