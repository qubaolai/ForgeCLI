"""Agent 运行事件的词汇 (ADR-0016 §3 / §4).

范围从 ADR-0010 §7.2 的"循环生命周期"扩到**整个 turn**: 模型调用, 工具排队, 安全裁决,
审批与执行都在同一条按 turn 编号的时间线上. 只读通知的**形状**属于领域 —— 换掉总线实现
(进程内分发 / 异步队列 / 落盘 trace) 不改变"一轮里会发生哪些事, 每件事带什么摘要".
总线与订阅端口是编排设施, 留在 application.

三条硬约束, 违反哪条都会把展示层变成安全旁路:

1. **不改变控制流.** 事件是观察, 不是 hook. 需要影响循环方向的能力必须实现 LoopHook.
2. **不替代持久化真相源.** `events.jsonl` + `state.json` 才是 resume 的依据;
   这里的东西活在进程内, 崩了就没了, 也**不应该**有 (ADR-0016 §9).
3. **payload 是封闭值对象, 不是 dict.** 自由 dict 会让 renderer 反过来猜 application
   的内部结构, 也拦不住"顺手把整个响应塞进去"这种事.

payload 里只放安全摘要: 不放 raw chain-of-thought, 不放凭证, 不放系统提示词, 不放未经
截断与脱敏的工具输出 (§6, §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.workspace.changes import WorkspaceChange

__all__ = [
    "AgentRunEvent",
    "AgentRunEventKind",
    "HumanPromptPayload",
    "ApprovalRequestedPayload",
    "ApprovalResolvedPayload",
    "ContextCompactedPayload",
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
    "TextDeltaPayload",
    "ToolCompletedPayload",
    "ToolPreparedPayload",
    "ToolQueuedPayload",
    "ToolStartedPayload",
    "TurnFinishedPayload",
    "WorkspaceChangedPayload",
]


class AgentRunEventKind(Enum):
    """运行事件类型 (§4). 值即序列化字符串, 进测试断言与可选 trace, 不可随意改."""

    # -- turn 与步骤 (§4.1) --
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

    # -- 上下文压缩 (ADR-0032 决策 1) --
    CONTEXT_COMPACTED = "context_compacted"

    # -- 工作区变化 --
    WORKSPACE_CHANGED = "workspace_changed"

    # -- 计划与待办 (ADR-0022 §7) --
    PLAN_PROPOSED = "plan_proposed"
    TODO_UPDATED = "todo_updated"

    # -- 工具, 安全与审批 (§4.3) --
    TOOL_QUEUED = "tool_queued"
    TOOL_PREPARED = "tool_prepared"
    POLICY_RESOLVED = "policy_resolved"
    PROMPT_REQUESTED = "prompt_requested"
    PROMPT_RESOLVED = "prompt_resolved"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_CANCELLED = "tool_cancelled"


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


@dataclass(frozen=True)
class HumanPromptPayload(RunEventPayload):
    prompt_id: str


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
    # 这次调用的用途 (RequestOrigin 的值). 二级压缩走 compact, Agent 自己的调用走
    # act / chat —— 展示层靠它把"这一笔是压缩烧的"标出来.
    #
    # 不标的话会露馅: 合计里含着一次压缩调用, 而"模型 N 次"数的是 MODEL_STARTED,
    # 压缩不发那个事件. 用户拿总数去核账单时, 会发现一次对不上的调用.
    origin: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    # 供应商没回 usage 时用的是本地估算 (§9). 估算值当成事实展示会让用户按它去核账单.
    estimated: bool = False

    @classmethod
    def from_draft(cls, draft: UsageRecordDraft) -> ModelUsagePayload:
        """由计量草稿造展示载荷.

        一个映射只写一处: 上下文压缩的用量与 Agent 自己调用的用量走的是两条不同的
        产出路径 (前者由 ContextManager 产出草稿, 后者由循环现场组装), 但展示层必须
        看到同一种形状 —— 各写一份, 迟早会有一边漏掉 cached 或 reasoning.
        """
        return cls(
            origin=draft.origin.value,
            input_tokens=draft.usage.input_tokens,
            output_tokens=draft.usage.output_tokens,
            reasoning_tokens=draft.usage.reasoning_tokens,
            cached_tokens=draft.usage.cached_input_tokens,
            total_tokens=draft.usage.total_tokens,
            estimated=draft.estimated,
        )


@dataclass(frozen=True)
class ContextCompactedPayload(RunEventPayload):
    """一次窗口淘汰 (ADR-0041 决策 5).

    这里的 token 数是**省下多少上下文**, 与同一次压缩发出的 ModelUsagePayload 是两个
    方向相反的数: 那个是这次压缩**花掉**多少. 两个数并排出现才读得懂 —— 只给省下的,
    看起来像是白赚的.

    tokens_before / tokens_after 都是 ApproximateTokenEstimator 对 transcript 的估算,
    不是供应商口径, 所以它们与用量行的数字本来就不该对得上.
    """

    level: str = ""
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    # 这次淘汰丢掉了几条消息.
    messages_replaced: int = 0
    # 交接说明用的模型; 没接网关时不调模型, 两项为空.
    provider: str = ""
    model: str = ""


@dataclass(frozen=True)
class WorkspaceChangedPayload(RunEventPayload):
    """一次模型调用前发现的工作区文件变化。"""

    changes: tuple[WorkspaceChange, ...] = ()


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
    """模型请求了一个工具, 带它**原样**提交的入参.

    这里的入参没经过 prepare 归一化, 所以展示时要让位给 TOOL_PREPARED 的那一份. 但它
    必须发出来: 一次连 prepare 都没走到的调用 (工具名不存在, schema 不合法) 只有排队
    和终态两条事件, 入参在别处一次都不会出现 —— 而"模型到底传了什么"正是这类失败唯一
    值得看的东西. 之前少了它, 一个 fs_write_file 的悬空调用在页面上只剩一个工具名.
    """

    tool_name: str
    # 同一批次里排在本调用之后的调用数。循环在第一个调用真正派发前按顺序发布整批；
    # 最后一条恒为 0，可作为批次已经发布完整的标志。
    queue_position: int = 0
    arguments: tuple[tuple[str, str], ...] = ()


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
    # 归一化后**真正会碰到**的路径 (超出上限时截断, target_count 仍是完整计数).
    # 只报个数的话, "影响 12 个目标"这句话没法核对: 用户要看的是哪 12 个.
    targets: tuple[str, ...] = ()
    workspace_scope: str = ""


@dataclass(frozen=True)
class PolicyResolvedPayload(RunEventPayload):
    """展示用的裁决摘要.

    它是给人看的那一份: 页面显示了"已允许"说的是裁决结论, 不是执行已经发生
    (§4.3).
    """

    tool_name: str
    decision: str
    reason: str
    mandatory: bool = False
    # 命中了哪条规则, 认定了哪些风险事实, 以及裁决自己给的那句话. 少了这三项, 一次
    # ask 或 deny 在页面上只有一个 reason 枚举值, 排查时看不出是规则还是分析器判的.
    matched_rule_id: str = ""
    risk_facts: tuple[str, ...] = ()
    detail: str = ""


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
    # 结构化的机制事实. result_summary 是给人一眼看的那一行, 这几项是给排查用的 ——
    # 从一句"1024 字节 · 退出码 1"里再解析出退出码, 是把展示格式当成了数据接口.
    error_code: str = ""
    exit_code: int | None = None
    bytes_out: int = 0
    artifact_count: int = 0
    truncated: bool = False
    # 这次调用有没有真的执行过. 工具不存在, prepare 失败, 被拒或没等到批准都会走到
    # 终态, 但它们与"执行了然后失败了"是两回事 (ADR-0004 §8).
    executed: bool = True


@dataclass(frozen=True)
class AgentRunEvent:
    """统一事件信封 (§3).

    sequence 由总线统一分配, 发布者不得自填 —— 所以这个类不该被业务代码直接构造,
    走 `AgentRunEventBus.publish`. 那不是风格偏好: 谁都能填 sequence 的话, 单调递增
    这条性质就没人保证得了, 而它正是"时间线可测"的全部依据.

    轮次由 ``(session_id, turn_id)`` 定位, 不是单独的 turn_id (ADR-0048 决策 2):
    turn 编号在每个会话里各自从 1 起, 所以两个会话都会有 ``turn_0001``. 少了
    session_id, 历史查询, 事件续传和界面都只能猜它属于谁, 而三者可以猜出不同答案.
    """

    event_id: str
    kind: AgentRunEventKind
    session_id: str
    turn_id: str
    sequence: int
    occurred_at: float
    payload: RunEventPayload = field(default_factory=RunEventPayload)
    step_index: int | None = None
    request_id: str | None = None
    tool_call_id: str | None = None
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("AgentRunEvent.session_id 不能为空")
        if not self.turn_id.strip():
            raise ValueError("AgentRunEvent.turn_id 不能为空")
        if self.sequence < 1:
            raise ValueError("AgentRunEvent.sequence 从 1 起递增")
        if not self.event_id.strip():
            raise ValueError("AgentRunEvent.event_id 不能为空")
