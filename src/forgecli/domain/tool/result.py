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

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

# 摘要的字符上限.
#
# 摘要是**唯一无条件进窗口**的那一段, 所以它必须是一行. shell_run 把整条命令,
# search_text 把整个 query 嵌进去, 而那两样都是模型写的, 长度不受控 —— 一段 heredoc
# 或一个几 KB 的正则就能让"省下正文"的那点额度被摘要自己吃回去.
MAX_SUMMARY_CHARS = 200

# 小于这个字符数的正文一律直接进窗口, 不换成句柄.
#
# 判据是**换成句柄划不划算**. 省下 B token 的收益是"往后每一轮少发 B"; 代价是模型要多发
# 一次 artifact_read, 而那一次往返要把当前整个上下文重发一遍. 按实测的上下文规模
# (5k~21k) 与每轮十来步算, 盈亏平衡大约落在 1500 token, 也就是四五千字符.
#
# 取 6000 而不是更小: 真实日志里被换成句柄又立刻取回的两条是 4210 与 3956 字节 —— 一次
# 目录列举和一次检索命中, 都是模型下一步就要用的东西. 定在它们之下, 等于每次检索都白
# 搭一个往返. 8 个会话 416 次工具调用里, artifact_read 占了 26 次 (6.2%), 全是二次取回.
_INLINE_ALWAYS_BELOW_CHARS = 6_000

__all__ = [
    "ArtifactRef",
    "ContentPart",
    "ResultProvenance",
    "ToolError",
    "ToolMetrics",
    "ToolResult",
    "ToolResultStatus",
    "TurnDisposition",
    "clip_for_summary",
]


def clip_for_summary(text: str, *, budget: int = MAX_SUMMARY_CHARS) -> str:
    """把一段不受控的文本裁到能进摘要的长度.

    显式截断并留下省略号: 悄悄截掉后半段, 模型会以为命令就是这么短的.
    """
    flat = " ".join(text.split())
    if len(flat) <= budget:
        return flat
    return f"{flat[: budget - 1]}…"


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
    """一次工具调用的结果, 分三段 (ADR-0041 决策 6).

    ``summary``  一行, 必填. 做了什么, 结果规模, 是否截断.
    ``data``     结构化小字段. 调用方不必再解析散文.
    ``content_parts``  不限长的正文. **默认不进会话窗口**, 溢写 artifact 后留句柄.

    分三段的理由是窗口增长的主项就是正文: 前三项加起来约三百 token 且可预测, 全部方差
    都来自正文. 把它拿出来单独定策, 窗口增长从约 1k/步 降到约 300/步.

    一个产不出简短 ``summary`` 的工具本身就是设计得不好 —— 这个必填把质量压力推到了它该
    在的地方. ``shell_run`` 一类跑任意命令的工具是明确的例外: 它给不出有意义的 ``data``,
    退化成 summary + 正文, 不硬编一份 schema.
    """

    invocation_id: str
    tool_name: str
    status: ToolResultStatus
    # 必填而不是给个默认值再运行期校验: 漏填的后果是模型看到一条没有结论的结果, 而那不会
    # 报错, 只会让它多调一次工具去问同一件事. 让类型检查在装配期就拦住.
    summary: str
    data: Mapping[str, object] = field(default_factory=dict)
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
        if not self.summary.strip():
            raise ValueError(f"{self.tool_name} 的 ToolResult.summary 不能为空")
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

    def raw_output(self) -> str:
        """工具跑出来的原始输出全文, 失败时并上错误消息.

        **给围栏用, 不给模型看** (ADR-0041 决策 9). `fence_hint` 正则扫的是命令实际
        输出里有没有越界痕迹, 拿摘要去扫等于把那道防线关掉.

        失败时把 ``ToolError.message`` 也拼进去: content_parts 装的是工具跑出来的输出,
        而一次 mkdir 失败根本没有输出 —— 只填 error 的结果回到手里就是一个空字符串,
        读不出发生过什么.
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

    def render_for_model(self, *, include_body: bool) -> str:
        """回填会话窗口的形态: 摘要 + 结构化字段 (+ 正文或句柄).

        ``include_body`` 来自 ``ToolSpec.body_in_window``, 逐工具声明. 但它只是**倾向**,
        不是最终决定 —— 下面两条压过它, 因为省掉正文本身也是有代价的:

        **① 没有句柄就必须带正文.** 不然内容等于被删了还不告诉人去哪找. 计划, 待办与
        记忆那几个工具都不归档 (``provenance`` 为 None), 少了这一条, ``plan_read`` 回给
        模型的就只有一句"读取计划", 正文凭空消失且无从取回.

        **② 小正文一律带上.** 省下一段 300 token 的正文, 换来的是模型多发一次
        ``artifact_read`` —— 而那一次往返要把**整个上下文**重发一遍. 省小的花大的,
        净效果是负的. 阈值按"一次往返的代价"定, 不是按"看起来长不长"定.
        """
        lines = [self.summary]
        if self.data:
            pairs = ", ".join(
                f"{key}={value}" for key, value in sorted(self.data.items())
            )
            lines.append(f"[{pairs}]")
        body = self.raw_output()
        handle = (
            self.provenance.artifact_id
            if self.provenance is not None and self.provenance.archived
            else ""
        )
        if include_body or not handle or len(body) <= _INLINE_ALWAYS_BELOW_CHARS:
            if body:
                lines.append(body)
            return "\n".join(line for line in lines if line)
        if self.error is not None:
            # 失败一律带上原因: 模型据此改方案, 而"退出 1"本身说明不了改什么.
            lines.append(self.error.message)
        lines.append(
            f"[完整输出已归档 {handle}"
            f" ({self.provenance.byte_size if self.provenance else 0} 字节),"
            " 需要细节时用 artifact_read 取回]"
        )
        return "\n".join(line for line in lines if line)

    def to_payload(self) -> dict[str, object]:
        """只记机制事实: 状态, 耗时, 退出码, 产物引用与截断情况."""
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
