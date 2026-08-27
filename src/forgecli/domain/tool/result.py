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
    "ResultProvenance",
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
class ResultProvenance:
    """这条结果是"什么东西, 在什么状态下"的一份快照 (ADR-0032 决策 3/4).

    两个字段组各自独立地开启一种能力, 缺一组不影响另一组:

    - ``source_path`` + ``source_state`` 让这条结果可以**去重**: 同一个路径在同一个
      状态下被读第二次, 后一次回一行引用就够了.
    - ``artifact_id`` 让这条结果可以**降级**: transcript 里的正文换成引用, 完整内容
      仍在 ArtifactStore 里, 用 artifact_read 取回.
    - ``mutated_paths`` 让这条结果可以**作废别人**: 写文件的工具据此告诉上下文管理,
      前面那些读到的内容从此不代表当前状态了.

    ``source_state`` 取 ``path_state_token``, 与 ADR-0027 执行前复核用的是**同一个
    函数**. 两套判据会让安全层说"没变"而去重层说"变了", 而这种分歧不会报错.

    只有读文件的工具填得出 ``source_*``. ``shell_run`` 填不出 —— 它的输出不是某个路径
    在某个状态下的快照, 重跑一次也不保证一样. 于是它的结果不参与去重, 但仍然可以降级.
    """

    artifact_id: str = ""
    source_path: str = ""
    source_state: str = ""
    byte_size: int = 0
    # 这次调用改掉了哪些路径 (决策 4). 与 ``source_*`` 分开而不是复用它:
    #
    # - 一个补丁信封可以动很多文件, ``source_path`` 只装得下一个.
    # - 写入的正文是一行改动说明, 不是文件内容. 让它去当"这份你读过"的引用目标, 模型
    #   顺着引用拿到的会是 "已应用 1 处改动", 而不是它要的那段代码.
    mutated_paths: tuple[str, ...] = ()

    @property
    def dedupable(self) -> bool:
        """能不能拿它判"这份内容我已经见过了"."""
        return bool(self.source_path and self.source_state)

    @property
    def archived(self) -> bool:
        """正文降级之后还取不取得回来."""
        return bool(self.artifact_id)


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
    # 这条结果的来源身份与归档位置 (ADR-0032). None 表示工具没有声明 —— 那样的结果
    # 既不去重也不降级, 原样留在 transcript 里.
    provenance: ResultProvenance | None = None

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
        """拼接成回填模型的文本 (截断片段带显式标记).

        失败时把 ``ToolError.message`` 也拼进去. content_parts 装的是工具跑出来的输出,
        而一次 mkdir 失败根本没有输出 —— 只填 error 的结果回到模型手里就是一个空字符串,
        它读不出发生过什么, 只能原样再试一次, 或者改用 shell 自己去摸文件系统.
        """
        lines = [
            f"{part.text}\n[输出已截断, 完整内容见产物 {part.artifact_id}]"
            if part.truncated
            else part.text
            for part in self.content_parts
        ]
        if self.error is not None:
            lines.append(self.error.message)
        return "\n".join(lines)

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
