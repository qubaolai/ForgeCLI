"""循环每次迭代的产出：LoopAction / LoopStop（ADR-0010 §4.3）。

AgentLoop 每步只能产出这三类结果之一，且都**只表达意图**，不执行任何副作用——真实执行
由 AgentTurnService 按 mode policy、approval、ToolRuntime 完成（§2 / §4.3 约束）。

- LoopAction：描述「想做什么」，采用密封子类型（对齐 domain.intents / gateway
  ContentBlock 的层次风格），每种动作一个类型，构造即有效。
- LoopStop：退出 / 暂停 / 错误隔离的唯一出口，reason 取自 LoopStopReason，可恢复与否由
  分类派生。

今日（2026-07-23）只冻契约与字段位，不接循环体与执行。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.tool.result import ResultProvenance


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
    # 这条观察对应的工具结果是"什么东西的快照" (ADR-0032). 循环原样转写到
    # ToolResultBlock 上, 供上下文管理判断能不能去重与降级. 循环自己不读它 ——
    # 它只是个传递者, 判断在 application/context.
    provenance: ResultProvenance | None = None
    # 这条结果正文的归档句柄, 空串表示没有归档. 循环拿它判"这次有没有带回新信息" ——
    # 句柄是内容寻址的, 所以正文不同则句柄不同, 比比对渲染文本准.
    handle: str = ""


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
class LoopStop:
    """退出 / 暂停 / 错误隔离的唯一出口（§4.3 / §6）。

    可恢复与否不存成字段: 它就是 reason.classification, 存一份副本只会多一处能对不上的
    地方。
    """

    reason: LoopStopReason
    message: str | None = None


# 循环每步的产出：决策（含待执行动作）或终止。驱动方（AgentTurnService）据此
# 执行动作、回填 observation，或结束本轮。
LoopStepResult = LoopAction | LoopStop | None
