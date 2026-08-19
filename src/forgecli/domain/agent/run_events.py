"""Agent 运行事件的词汇 (ADR-0016 §3 / §4).

范围从 ADR-0010 §7.2 的"循环生命周期"扩到**整个 turn**: 模型调用, 工具排队, 安全裁决,
审批与执行都在同一条按 turn 编号的时间线上. 只读通知的**形状**属于领域 —— 换掉总线实现
(进程内分发 / 异步队列 / 落盘 trace) 不改变"一轮里会发生哪些事, 每件事带什么摘要".
总线与订阅端口是编排设施, 留在 application.

三条硬约束, 违反哪条都会把展示层变成安全旁路:

1. **不改变控制流.** 事件是观察, 不是 hook. 需要影响循环方向的能力必须实现 LoopHook.
2. **不替代持久化真相源.** `events.jsonl` + `state.json` 才是 resume 与审计的依据;
   这里的东西活在进程内, 崩了就没了, 也**不应该**有 (ADR-0016 §9).
3. **payload 是封闭值对象, 不是 dict.** 自由 dict 会让 renderer 反过来猜 application
   的内部结构, 也拦不住"顺手把整个响应塞进去"这种事.

payload 里只放安全摘要: 不放 raw chain-of-thought, 不放凭证, 不放系统提示词, 不放未经
截断与脱敏的工具输出 (§6, §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "AgentRunEvent",
    "AgentRunEventKind",
    "ApprovalRequestedPayload",
    "ApprovalResolvedPayload",
    "DecisionSummaryPayload",
    "ModelCompletedPayload",
    "PlanProposedPayload",
    "TodoUpdatedPayload",
    "ModelFailedPayload",
    "ModelStartedPayload",
    "ModelUsagePayload",
    "PolicyResolvedPayload",
    "ReasoningStatus",
    "ReasoningStatusPayload",
    "RunEventPayload",
    "RunPhase",
    "StepStartedPayload",
    "TextDeltaPayload",
    "ToolCompletedPayload",
    "ToolPreparedPayload",
    "ToolQueuedPayload",
    "ToolStartedPayload",
    "TurnFinishedPayload",
    "TurnStartedPayload",
]


class AgentRunEventKind(Enum):
    """运行事件类型 (§4). 值即序列化字符串, 进测试断言与可选 trace, 不可随意改."""

    # -- turn 与步骤 (§4.1) --
    TURN_STARTED = "turn_started"
    STEP_STARTED = "step_started"
    DECISION_SUMMARY = "decision_summary"
    TURN_COMPLETED = "turn_completed"
    TURN_CANCELLED = "turn_cancelled"
    TURN_FAILED = "turn_failed"

    # -- 模型调用 (§4.2) --
    MODEL_STARTED = "model_started"
    MODEL_OUTPUT_DELTA = "model_output_delta"
    MODEL_REASONING_STATUS = "model_reasoning_status"
    MODEL_COMPLETED = "model_completed"
    MODEL_USAGE = "model_usage"
    MODEL_FAILED = "model_failed"

    # -- 计划与待办 (ADR-0022 §7) --
    PLAN_PROPOSED = "plan_proposed"
    TODO_UPDATED = "todo_updated"

    # -- 工具, 安全与审批 (§4.3) --
    TOOL_QUEUED = "tool_queued"
    TOOL_PREPARED = "tool_prepared"
    POLICY_RESOLVED = "policy_resolved"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_CANCELLED = "tool_cancelled"


class RunPhase(Enum):
    """当前这一步在做什么, 决定终端活动区显示哪种状态."""

    THINKING = "thinking"
    TOOL = "tool"
    ANSWER = "answer"


class ReasoningStatus(Enum):
    """思考状态.

    只报状态, 不报内容: 供应商开了 thinking 却不返回可展示摘要是常态, 终端能说的就是
    "在想", 绝不从回答或 token 数倒推一段思维链出来 (ADR-0016 §6).
    """

    STARTED = "started"
    COMPLETED = "completed"


@dataclass(frozen=True)
class RunEventPayload:
    """事件载荷的密封基类. 子类都是冻结值对象, 不允许自由 dict."""


# ---- turn 与步骤 ----


@dataclass(frozen=True)
class TurnStartedPayload(RunEventPayload):
    mode: str
    tool_count: int


@dataclass(frozen=True)
class StepStartedPayload(RunEventPayload):
    step_index: int
    phase: RunPhase


@dataclass(frozen=True)
class DecisionSummaryPayload(RunEventPayload):
    """ForgeCLI 自己生成的可审计行动摘要.

    与供应商 reasoning 分开命名不是洁癖: 一个是我们能解释, 能审计, 能复现的结构化决策,
    另一个是模型内部产物. 混成一栏展示, 用户会以为看到的是模型在想什么 (§6).
    """

    reason_summary: str


@dataclass(frozen=True)
class TurnFinishedPayload(RunEventPayload):
    """TURN_COMPLETED / TURN_CANCELLED / TURN_FAILED 共用."""

    status: str
    elapsed_ms: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    detail: str = ""


# ---- 模型调用 ----


@dataclass(frozen=True)
class ModelStartedPayload(RunEventPayload):
    """call_index 是本轮第几次调模型: 终端据此把多次调用分成独立的块 (§5)."""

    call_index: int
    provider: str = ""
    model: str = ""


@dataclass(frozen=True)
class TextDeltaPayload(RunEventPayload):
    """模型可见输出的一段增量."""

    text: str


@dataclass(frozen=True)
class ReasoningStatusPayload(RunEventPayload):
    status: ReasoningStatus


@dataclass(frozen=True)
class ModelCompletedPayload(RunEventPayload):
    finish_reason: str
    text_chars: int = 0
    tool_call_count: int = 0
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class ModelUsagePayload(RunEventPayload):
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    # 供应商没回 usage 时用的是本地估算 (§9). 估算值当成事实展示会让用户按它去核账单.
    estimated: bool = False

    @property
    def total(self) -> int:
        """本次调用的 token 总数.

        供应商给了就用供应商的: 各家对"总数"的口径不一样 (reasoning token 有的并进
        output, 有的单列), 自己把几项加起来会和账单对不上. 没给才退回 input + output.
        """
        return self.total_tokens or (self.input_tokens + self.output_tokens)


@dataclass(frozen=True)
class ModelFailedPayload(RunEventPayload):
    """error_kind 是归一化分类, 不是异常类名: 终端不能靠字符串匹配认错误 (§10.2)."""

    error_kind: str
    message: str
    retryable: bool = False


# ---- 计划与待办 ----


@dataclass(frozen=True)
class PlanProposedPayload(RunEventPayload):
    """模型提交了一份计划.

    只带摘要: 正文由 PlanReviewPrompt 现取现打. 让事件也带一份正文, 终端就有两个来源,
    而它们迟早会不一致.
    """

    plan_id: str
    title: str
    revision: int = 1
    step_count: int = 0


@dataclass(frozen=True)
class TodoUpdatedPayload(RunEventPayload):
    """待办被改了.

    带 current 是因为终端要显示的正是"现在该做哪一步" —— 那一行比 3/5 这个比例更有用.
    """

    todo_id: str
    done: int = 0
    total: int = 0
    current: str = ""


# ---- 工具, 安全与审批 ----


@dataclass(frozen=True)
class ToolQueuedPayload(RunEventPayload):
    """模型请求了一个工具.

    这里只带工具名与队列位置; 入参随 TOOL_PREPARED 一起给出 —— 排队那一刻的入参还没
    经过 prepare 归一化, 展示它容易与真正执行的东西对不上.
    """

    tool_name: str
    queue_position: int = 0


@dataclass(frozen=True)
class ToolPreparedPayload(RunEventPayload):
    """归一化后的计划摘要, 外加本次调用的入参.

    arguments 是模型请求的原始入参, 逐字展示. 参数里可能出现什么, 用户就该看到什么 ——
    终端是他判断"要不要让这次调用发生"的唯一依据.
    """

    tool_name: str
    capabilities: tuple[str, ...] = ()
    target_count: int = 0
    target_resolution: str = ""
    arguments: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PolicyResolvedPayload(RunEventPayload):
    """展示用的裁决摘要.

    它**不替代**持久化的 policy_decision 审计: 终端显示了"已允许"不等于审计已落盘
    (§4.3).
    """

    tool_name: str
    decision: str
    reason: str
    mandatory: bool = False


@dataclass(frozen=True)
class ApprovalRequestedPayload(RunEventPayload):
    tool_name: str
    mandatory: bool = False
    target_count: int = 0


@dataclass(frozen=True)
class ApprovalResolvedPayload(RunEventPayload):
    tool_name: str
    outcome: str
    scope: str = ""


@dataclass(frozen=True)
class ToolStartedPayload(RunEventPayload):
    tool_name: str


@dataclass(frozen=True)
class ToolCompletedPayload(RunEventPayload):
    """TOOL_COMPLETED / TOOL_CANCELLED 共用.

    side_effect_unknown 对应 ADR-0004 的 outcome_unknown: 执行中被打断且不幂等时,
    "没成功"与"没发生"是两回事, 终端不能把它显示成普通失败 (§10.1).
    """

    tool_name: str
    status: str
    elapsed_ms: float = 0.0
    result_summary: str = ""
    error_summary: str = ""
    side_effect_unknown: bool = False


@dataclass(frozen=True)
class AgentRunEvent:
    """统一事件信封 (§3).

    sequence 由总线统一分配, 发布者不得自填 —— 所以这个类不该被业务代码直接构造,
    走 `AgentRunEventBus.publish`. 那不是风格偏好: 谁都能填 sequence 的话, 单调递增
    这条性质就没人保证得了, 而它正是"时间线可测"的全部依据.
    """

    event_id: str
    kind: AgentRunEventKind
    turn_id: str
    sequence: int
    occurred_at: float
    payload: RunEventPayload = field(default_factory=RunEventPayload)
    step_index: int | None = None
    request_id: str | None = None
    tool_call_id: str | None = None
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("AgentRunEvent.turn_id 不能为空")
        if self.sequence < 1:
            raise ValueError("AgentRunEvent.sequence 从 1 起递增")
        if not self.event_id.strip():
            raise ValueError("AgentRunEvent.event_id 不能为空")
