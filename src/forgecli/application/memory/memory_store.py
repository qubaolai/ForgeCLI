"""记忆的读写端口与上限 (ADR-0033 决策 4).

一个 scope 一份存储. 读写规则 (多大算超限, 能存几条) 属 application; 真的去碰文件
系统属 infrastructure.

上限写在这里而不是实现里: 它们是**决策**不是实现细节, 换一个 store 实现不该顺带换掉
"单条 512 字节, 每级 32 条"这条约束. 与 ADR-0018 把 FORGE.md 的上限写在 reader 端口
上是同一个理由.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.memory.entry import MemoryEntry

__all__ = [
    "MAX_ENTRIES_PER_SCOPE",
    "MAX_VALUE_BYTES",
    "MemoryStore",
]

# 单条记忆的正文上限.
#
# 512 字节而不是更宽: 记忆**每一轮都进提示词**, 与 FORGE.md 那 32 KiB 的一次性预算
# 不是一回事. 一条写成小作文的记忆会在此后每一次模型调用上收费.
MAX_VALUE_BYTES = 512

# 每级最多存几条. 满了拒绝写入并告诉模型先 memory_forget 一条.
#
# 拒绝而不是自动淘汰最旧的: 淘汰要挑一个"最不重要"的, 而这里没有任何依据能挑对 ——
# 最旧的那条完全可能是最要紧的那条 (项目的测试命令一次就记对了, 之后再没动过).
MAX_ENTRIES_PER_SCOPE = 32


class MemoryStore(ABC):
    """一个 scope 的记忆存储."""

    @abstractmethod
    def load(self) -> tuple[MemoryEntry, ...]:
        """读出全部记忆. 读不到, 格式坏掉的一律当作空, 不抛错.

        记忆是辅助机制: 一份读不了的 memory.json 不该让整个会话起不来 (与
        ProjectInstructionReader 同一条理由).
        """

    @abstractmethod
    def save(self, entries: tuple[MemoryEntry, ...]) -> None:
        """整表写回."""
