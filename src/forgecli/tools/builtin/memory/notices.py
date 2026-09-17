"""memory 工具回给模型的那几句: 正文在 prompts.py, 这里只做映射."""

from __future__ import annotations

from forgecli.memory.service import MemoryRejection
from forgecli.memory.store import MAX_ENTRIES_PER_SCOPE, MAX_VALUE_BYTES
from forgecli.prompt import prompts

__all__ = ["rejection_text"]


def rejection_text(rejection: MemoryRejection) -> str:
    """拒绝原因 -> 给模型的说明. 每一句都有模板, 没有的是 bug."""
    if rejection is MemoryRejection.INVALID_KEY:
        return prompts.MEMORY_REJECT_INVALID_KEY
    if rejection is MemoryRejection.EMPTY_VALUE:
        return prompts.MEMORY_REJECT_EMPTY_VALUE
    if rejection is MemoryRejection.VALUE_TOO_LONG:
        return prompts.memory_reject_value_too_long(MAX_VALUE_BYTES)
    if rejection is MemoryRejection.LOOKS_LIKE_SECRET:
        return prompts.MEMORY_REJECT_SECRET
    return prompts.memory_reject_scope_full(MAX_ENTRIES_PER_SCOPE)
