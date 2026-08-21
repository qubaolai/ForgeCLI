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
    "TurnDisposition",
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


class TurnDisposition(Enum):
    """工具对**本次 turn 该怎么继续**的声明 (ADR-0023 决策 1).

    这是工具唯一能影响循环走向的字段, 它的作用范围被刻意定得极窄:

    **只能缩短一次 turn, 不能延长, 不能授予能力.** 失效方向朝安全 —— 将来一个行为不端的
    MCP 工具把它置上, 后果是多问一次人, 不是绕过任何检查.

    循环按这个**字段**分流, 不去读 content 里的文字, 也不认识任何工具名. 于是同一套机制
    对将来的 `ask_user` 一类工具同样成立.
    """

    CONTINUE = "continue"
    # 这次输出需要人裁决. 循环在回合边界停下, 由 CLI 驱动交互.
    AWAIT_USER_DECISION = "await_user_decision"


@dataclass(frozen=True)
class ToolResult:
    invocation_id: str
    tool_name: str
    status: ToolResultStatus
    content_parts: tuple[ContentPart, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    metrics: ToolMetrics = field(default_factory=ToolMetrics)
    error: ToolError | None = None
    # None 表示无法证明（例如 shell 超时后可能已部分写入）；False 只用于能证明副作用尚未
    # 发生的失败。恢复协调器据此避免把竞争者创建的文件误记成 Forge 的 postimage。
    workspace_mutated: bool | None = None
    turn_disposition: TurnDisposition = TurnDisposition.CONTINUE

    def __post_init__(self) -> None:
        if self.status is ToolResultStatus.OK and self.error is not None:
            raise ValueError("status=ok 的结果不能带 error")
        if self.status is not ToolResultStatus.OK and self.error is None:
            raise ValueError(f"status={self.status.value} 的结果必须带 error")
        if (
            self.turn_disposition is TurnDisposition.AWAIT_USER_DECISION
            and self.status is not ToolResultStatus.OK
        ):
            # 失败的调用没有可供人裁决的产出. 允许它停下, 人看到的会是一个空的评审界面.
            raise ValueError("只有成功的结果才能要求人裁决")

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
            "workspace_mutated": self.workspace_mutated,
        }
