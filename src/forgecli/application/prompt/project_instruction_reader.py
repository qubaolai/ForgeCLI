"""FORGE.md 的读取端口与结果 DTO (ADR-0018 §5).

读取规则 (查哪些路径, 多大算超限, 怎么标信任) 属 application; 真的去碰文件系统属
infrastructure. 端口在这里, 实现在 infrastructure/prompt.

上限写在这里而不是实现里: 它是**决策**不是实现细节, 换一个 reader 实现不该顺带换掉
"单文件 32 KiB, 总计 64 KiB"这条约束.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

__all__ = [
    "INSTRUCTION_FILE_NAME",
    "MAX_INSTRUCTION_BYTES",
    "MAX_TOTAL_INSTRUCTION_BYTES",
    "ProjectInstruction",
    "ProjectInstructionReader",
]

INSTRUCTION_FILE_NAME = "FORGE.md"
MAX_INSTRUCTION_BYTES = 32 * 1024
MAX_TOTAL_INSTRUCTION_BYTES = 64 * 1024

# 截断标记. 显式加进正文, 不静默截断 —— 模型据此知道自己看到的不是全部.
TRUNCATION_NOTICE = "[FORGE.md 已截断]"


@dataclass(frozen=True)
class ProjectInstruction:
    """一份读到的项目指令. 已经过大小与编码校验.

    没有 truncated 标志: 截断这件事由正文末尾的 TRUNCATION_NOTICE 表达, 而那是模型真正
    看得到的那一份. 再挂一个布尔就是同一个事实的第二份副本, 且没有消费方 —— ADR-0018
    §10.1 要的 project_instructions_truncated 属于阶段 4 的诊断接线, 那时再从正文取.
    """

    source_id: str
    text: str
    digest: str

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("ProjectInstruction.source_id 不能为空")


class ProjectInstructionReader(ABC):
    """读取受信任根目录顶层的 FORGE.md."""

    @abstractmethod
    def read(self, workspace_roots: tuple[str, ...]) -> tuple[ProjectInstruction, ...]:
        """按给定根的顺序读取. 读不到, 越界, 非 UTF-8 的一律跳过, 不抛错.

        提示词是辅助机制: 一份读不了的 FORGE.md 不该让整个会话起不来 (ADR-0018 §11).
        """
