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
from types import MappingProxyType

from forgecli.application.agent_loop.stop import LoopStopReason



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


@dataclass(frozen=True)
class ApprovalRequest:
    """审批请求（§4.3）。必须能映射到用户可理解的风险说明。

    action_summary 说明「要做什么」，risk_summary 说明「风险在哪」，二者都只含安全摘要；
    tool_request 为触发审批的工具请求（若来自工具）。
    """
    action_summary: str
    risk_summary: str
    tool_request: ToolRequest | None = None

    def __post_init__(self) -> None:
        if not self.action_summary.strip():
            raise ValueError("ApprovalRequest.action_summary 不能为空")
        if not self.risk_summary.strip():
            raise ValueError("ApprovalRequest.risk_summary 不能为空")
        

@dataclass(frozen=True)
class LoopObservation:
    """喂回循环的观察（§4.2）。只保存工具结果、用户反馈、错误、安全摘要或 context 信息。

    source 标注观察来源（tool / user / error / context 等，自由文本摘要）；is_error 标记
    工具失败一类的错误观察。不保存 raw chain-of-thought。
    """

    content: str
    source: str = ""
    is_error: bool = False


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
class AskUserAction(LoopAction):
    """请求用户补充信息。"""

    prompt: str

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("AskUserAction.prompt 不能为空")
        

@dataclass(frozen=True)
class ApprovalRequestAction(LoopAction):
    """请求对某动作进行人类审批。"""

    request: ApprovalRequest


@dataclass(frozen=True)
class CompactionRequestAction(LoopAction):
    """请求先做上下文压缩，再恢复执行。"""

    reason: str = ""


@dataclass(frozen=True)
class LoopDecision:
    """一次迭代的决策：为什么下一步这样做（§4.3）。

    reason_summary 只写可审计摘要（不落 raw CoT）；next_action 为本步要执行的动作
    （None 表示纯反思 / 继续观察）；continue_reason 说明为何继续而非停止。
    """

    reason_summary: str | None = None
    next_action: LoopAction | None = None
    continue_reason: str | None = None


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
        return cls(reason=reason, message = message, resumable = derived)
    

# 循环每步的产出：决策（含待执行动作）或终止。驱动方（AgentTurnService）据此
# 执行动作、回填 observation，或结束本轮。
LoopStepResult = LoopDecision | LoopStop
