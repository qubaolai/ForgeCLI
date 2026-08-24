"""ArtifactStore: 超阈值输出的落盘位置 (ADR-0004 §8).

产物不落在工作区: 一次跑测试产生的 20MB 日志写进用户仓库, 既污染工作区又会被下一次
Agent 读回上下文. 它落在 Forge 状态目录, 事件里只留引用, 大小和哈希.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.tool.result import ArtifactRef

__all__ = ["ArtifactRef", "ArtifactStore"]


class ArtifactStore(ABC):
    @abstractmethod
    def write(self, *, invocation_id: str, name: str, data: str) -> ArtifactRef:
        """落盘一段输出并返回引用."""

    @abstractmethod
    def read(self, artifact_id: str) -> str:
        """读回产物内容 (供 /artifact 查看, 不自动回填模型)."""
