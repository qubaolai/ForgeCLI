"""循环每次迭代的产出：LoopDecision / LoopAction / LoopStop（ADR-0010 §4.3）。

AgentLoop 每步只能产出这三类结果之一，且都**只表达意图**，不执行任何副作用——真实执行
由 AgentTurnService 按 mode policy、approval、ToolRuntime 完成（§2 / §4.3 约束）。

- LoopDecision：描述「为什么下一步这样做」，只写可审计摘要，不落 raw chain-of-thought。
- LoopAction：描述「想做什么」，采用密封子类型（对齐 domain.intents / gateway
  ContentBlock 的层次风格），每种动作一个类型，构造即有效。
- LoopStop：退出 / 暂停 / 错误隔离的唯一出口，reason 取自 LoopStopReason，resumable 由
  分类派生。

今日（2026-07-23）只冻契约与字段位，不接循环体与执行。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from forgecli.domain.agent.stop import LoopStopReason


@dataclass(frozen=True)
class ToolRequest:
    """一次工具调用意图（§4.3）。必须经 Tool Registry 执行，不能携带可执行匿名代码块。

    tool_call_id 关联模型侧 tool call 与后续 tool result；arguments 为未执行的原始入参，
    执行前仍须经 schema 校验与 mode policy 裁决，不可直接信任。
    """

    name: str
    arguments: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ToolRequest.name 不能为空")


class ObservationSource(Enum):
    """观察来源. 用枚举而不是自由文本: 审计与测试要能稳定断言它."""

    TOOL = "tool"
    USER = "user"
    ERROR = "error"
    SECURITY = "security"
    CONTEXT = "context"


class ObservationDisposition(Enum):
    """这条观察对**后续工具调用**意味着什么.

    它与 is_error 是两件事: 一次读文件失败是错误但可以重试, 一次人类拒绝不是错误却
    必须停下. 循环靠这个字段分流, 而不是去读 content 里的文字.
    """

    CONTINUE = "continue"
    # 这条路不通, 换条路是合理的 (策略拒绝, 需要重新审批). 计入本轮拒绝预算.
    BLOCKED = "blocked"
    # 本轮不该再派工具了 (人类明确拒绝, 或根本无人可裁决).
    HALT = "halt"
    # 工具产出了需要人裁决的东西, 本轮到此为止, 由 CLI 驱动交互 (ADR-0023).
    #
    # 与 HALT 的区别是**为什么停**: HALT 是"人已经说了不", 这里是"该轮到人说话了".
    # 两者都不再派工具, 但 HALT 之后模型要解释自己原本想做什么, 而这里模型已经把要说的
    # 说完了 —— 它交出了一份计划, 正等着回话.
    AWAIT_USER_DECISION = "await_user_decision"


@dataclass(frozen=True)
class LoopObservation:
    """喂回循环的观察（§4.2）。只保存工具结果、用户反馈、错误、安全摘要或 context 信息。

    is_error 标记工具失败一类的错误观察。不保存 raw chain-of-thought。
    """

    content: str
    source: ObservationSource = ObservationSource.CONTEXT
    is_error: bool = False
    disposition: ObservationDisposition = ObservationDisposition.CONTINUE


@dataclass(frozen=True)
class LoopAction:
    """循环动作密封基类。只表达意图，不执行副作用。子类型见下。"""


@dataclass(frozen=True)
class AnswerAction(LoopAction):
    """产出最终 assistant message（对应 LoopStopReason.FINAL_ANSWER 前的动作）。"""

    text: str


@dataclass(frozen=True)
class ToolRequestAction(LoopAction):
    """请求执行一个工具（由 AgentTurnService 裁决并分发）。"""

    request: ToolRequest


@dataclass(frozen=True)
class LoopDecision:
    """一次迭代的决策：为什么下一步这样做（§4.3）。

    reason_summary 只写可审计摘要（不落 raw CoT）；next_action 为本步要执行的动作
    （None 表示纯反思 / 继续观察）；continue_reason 说明为何继续而非停止。
    """

    reason_summary: str | None = None
    next_action: LoopAction | None = None


@dataclass(frozen=True)
class LoopStop:
    """退出 / 暂停 / 错误隔离的唯一出口（§4.3 / §6）。

    resumable 默认由 reason 的分类派生（可恢复暂停 → True）；用 of() 构造时自动填充，
    也允许显式覆盖（如预算策略对 BUDGET_EXHAUSTED 的裁定）。
    """

    reason: LoopStopReason
    message: str | None = None
    resumable: bool = False

    @classmethod
    def of(
        cls,
        reason: LoopStopReason,
        message: str | None = None,
        *,
        resumable: bool | None = None,
    ) -> LoopStop:
        """按分类派生 resumable 构造 LoopStop；resumable 显式传入时以传入为准。"""
        derived = reason.is_resumable_pause if resumable is None else resumable
        return cls(reason=reason, message=message, resumable=derived)


# 循环每步的产出：决策（含待执行动作）或终止。驱动方（AgentTurnService）据此
# 执行动作、回填 observation，或结束本轮。
LoopStepResult = LoopDecision | LoopStop
