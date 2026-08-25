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
    """压到了第几级. 值即落盘字符串."""

    # 一级: 把 tool result 正文换成 artifact 引用. 确定性, 不花钱, 3 天内可回取.
    DOWNGRADE = "downgrade"
    # 二级: 用模型把一批消息换成一段摘要. 不可复现.
    SUMMARY = "summary"


@dataclass(frozen=True)
class CompactionDraft:
    """一次压缩的记录. 摘要正文进 payload, 不留引用.

    这一条与 ADR-0022 计划正文"只记引用"的取舍相反, 理由是**可复现性**: 计划正文的
    真相源是磁盘上的文件, 随时读得回来; 而摘要是模型一次性产出的, 引用的目标一旦被
    回收就永远重建不出来, 那条会话的 ``/resume`` 从此少一段历史.
    """

    level: CompactionLevel
    tokens_before: int
    tokens_after: int
    # 一级降级改写了几个 tool result 块.
    blocks_rewritten: int = 0
    # 二级摘要顶替了几条消息.
    messages_replaced: int = 0
    summary: str = ""
    provider: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if self.tokens_before < 0 or self.tokens_after < 0:
            raise ValueError("CompactionDraft 的 token 数不能为负")
        if self.level is CompactionLevel.SUMMARY and not self.summary.strip():
            raise ValueError("SUMMARY 级压缩必须带摘要正文")

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    def to_payload(self) -> dict[str, object]:
        return {
            "level": self.level.value,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "tokens_saved": self.tokens_saved,
            "blocks_rewritten": self.blocks_rewritten,
            "messages_replaced": self.messages_replaced,
            "summary": self.summary,
            "provider": self.provider,
            "model": self.model,
        }
