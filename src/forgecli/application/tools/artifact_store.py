"""ArtifactStore: 超阈值输出的落盘位置 (ADR-0004 §8).

产物不落在工作区: 一次跑测试产生的 20MB 日志写进用户仓库, 既污染工作区又会被下一次
Agent 读回上下文. 它落在 Forge 状态目录, 事件里只留引用, 大小和哈希.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.tool.hashing import digest_text
from forgecli.domain.tool.result import ArtifactRef

__all__ = ["ArtifactStore", "NullArtifactStore"]


class ArtifactStore(ABC):
    @abstractmethod
    def write(self, *, invocation_id: str, name: str, data: str) -> ArtifactRef:
        """落盘一段输出并返回引用."""

    @abstractmethod
    def read(self, artifact_id: str) -> str:
        """读回产物内容 (供 /artifact 查看, 不自动回填模型)."""


class NullArtifactStore(ArtifactStore):
    """不落盘的实现: 内容直接丢弃, 只保留大小与哈希.

    用于纯内存测试和"用户明确不想留产物"的场景. 它仍然如实报告 truncated, 不会假装
    输出完整 —— 丢内容可以, 骗调用方不行.
    """

    def write(self, *, invocation_id: str, name: str, data: str) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=f"{invocation_id}:{name}",
            path="",
            size=len(data.encode("utf-8")),
            content_hash=digest_text(data),
            truncated=True,
        )

    def read(self, artifact_id: str) -> str:
        raise KeyError(f"NullArtifactStore 不保存内容: {artifact_id}")
