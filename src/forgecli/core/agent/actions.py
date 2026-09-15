"""循环与外界交换的值对象 (ADR-0010 §4, ADR-0051).

- ToolRequest: 循环请求执行一个工具. 必须经 dispatcher 走安全管线, 循环自己不执行.
- LoopObservation: 一次工具执行的结论, 喂回循环. 只保存结论文本与处置意见, 不保存
  raw chain-of-thought.
- LoopStop: 一轮为什么停. reason 取自 LoopStopReason, 可恢复与否由分类派生.
- LoopResult: 一轮跑完交给外层的全部产出.
- ToolExecutor / ContextAssembly: 外层交给循环的两个函数 —— 执行一次工具, 组一次
  上下文. 循环拿到的只是函数, 认识不到 dispatcher, 更认识不到注册表, 运行时与各个
  服务; 单一入口由 dispatcher 的类型守着.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from forgecli.core.agent.state import AssembledContext
from forgecli.core.agent.stop import LoopStopReason
from forgecli.core.context.compaction import CompactionDraft
from forgecli.core.message import ChatMessage
from forgecli.core.model.usage import UsageRecordDraft
from forgecli.core.tool.result import ResultProvenance


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
class LoopStop:
    """退出 / 暂停 / 错误隔离的唯一出口（§4.3 / §6）。

    可恢复与否不存成字段: 它就是 reason.classification, 存一份副本只会多一处能对不上的
    地方。
    """

    reason: LoopStopReason
    message: str | None = None


@dataclass(frozen=True)
class LoopResult:
    """一轮跑完交给外层的产出. 外层只读它, 不再持有循环实例."""

    stop: LoopStop
    # 模型最后那次回答的正文; 没走到回答就停的话为 None.
    answer: str | None = None
    # 流式中断时已累积的部分回答 (无则 None).
    partial_answer: str | None = None
    # 结束时的窗口, 外层接着往下攒 (ADR-0041 决策 4).
    window: tuple[ChatMessage, ...] = ()
    usage_drafts: tuple[UsageRecordDraft, ...] = ()
    compaction_drafts: tuple[CompactionDraft, ...] = ()


# 外层交给循环的"执行一次工具": AgentTurnService 那个经 dispatcher 走安全管线的方法.
ToolExecutor = Callable[[ToolRequest], LoopObservation]

# 外层交给循环的"组一次上下文". 循环每次调模型前调它一次 (ADR-0051 决策 2).
ContextAssembly = Callable[[], AssembledContext]
