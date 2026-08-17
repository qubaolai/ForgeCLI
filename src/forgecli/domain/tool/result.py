"""ToolResult: 归一化的执行结果 (ADR-0004 §8).

两条边界:

- **策略结果不是 ToolResult.** policy_denied, approval_required 和
  execution_environment_changed 由协调器产出为 observation. 错误码空间分开, 模型才不会
  把"被拒绝"当成"工具坏了"反复重试.
- **工具输出一律不可信.** 文件内容, 命令输出和 MCP 响应都可能夹带针对模型的指令.
  provenance / trust 做成 ClassVar 而不是字段: 它们没有第二个取值, 留成可配置字段等于
  给"某些工具的输出可信"开后门.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

__all__ = [
    "ArtifactRef",
    "ContentPart",
    "ToolError",
    "ToolMetrics",
    "ToolResult",
    "ToolResultStatus",
]


class ToolResultStatus(Enum):
    OK = "ok"
    INVALID_INPUT = "invalid_input"
    TOOL_ERROR = "tool_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"


@dataclass(frozen=True)
class ContentPart:
    """一段结果内容. 回填模型时必须带着截断标记, 不做静默截断."""

    text: str
    media_type: str = "text/plain"
    truncated: bool = False
    artifact_id: str | None = None

    provenance: ClassVar[str] = "tool_output"
    trust: ClassVar[str] = "untrusted"


@dataclass(frozen=True)
class ArtifactRef:
    """溢写到产物存储的输出引用. 事件里只留引用, 大小和哈希."""

    artifact_id: str
    path: str
    size: int
    content_hash: str
    truncated: bool = False


@dataclass(frozen=True)
class ToolMetrics:
    duration_seconds: float = 0.0
    exit_code: int | None = None
    bytes_out: int = 0
    child_process_count: int = 0


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ToolResult:
    invocation_id: str
    tool_name: str
    status: ToolResultStatus
    content_parts: tuple[ContentPart, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    metrics: ToolMetrics = field(default_factory=ToolMetrics)
    error: ToolError | None = None

    def __post_init__(self) -> None:
        if self.status is ToolResultStatus.OK and self.error is not None:
            raise ValueError("status=ok 的结果不能带 error")
        if self.status is not ToolResultStatus.OK and self.error is None:
            raise ValueError(f"status={self.status.value} 的结果必须带 error")

    @property
    def text(self) -> str:
        """拼接成回填模型的文本 (截断片段带显式标记)."""
        return "\n".join(
            f"{part.text}\n[输出已截断, 完整内容见产物 {part.artifact_id}]"
            if part.truncated
            else part.text
            for part in self.content_parts
        )

    def to_audit_payload(self) -> dict[str, object]:
        """工具侧审计只记机制事实: 状态, 耗时, 退出码, 产物引用与截断情况."""
        return {
            "invocation_id": self.invocation_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "duration_seconds": self.metrics.duration_seconds,
            "exit_code": self.metrics.exit_code,
            "bytes_out": self.metrics.bytes_out,
            "child_process_count": self.metrics.child_process_count,
            "artifact_ids": [artifact.artifact_id for artifact in self.artifacts],
            "truncated": any(part.truncated for part in self.content_parts),
            "error_code": self.error.code if self.error else None,
        }
