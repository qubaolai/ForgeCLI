"""BuiltinAgentLoop: MVP 唯一的 ReAct 循环实现 (ADR-0010 §3 / §5 / §13-3).

一轮的形状:

    模型调用 -> 有 tool_calls?
      ├─ 是: 逐个产出 ToolRequestAction, 每拿回一条 observation 就写进 transcript,
      │      全部回填完再调一次模型 (这就是 ReAct 的 act -> observe -> reason 环)
      └─ 否: AnswerAction -> observe 后 LoopStop(FINAL_ANSWER)

三条与安全直接相关的约束:

- **循环只产出意图, 不执行任何副作用.** ToolRequestAction 交给 AgentTurnService, 由它
  经 ToolRequestCoordinator 走完整条安全管线. 循环拿不到 ToolRegistry, 也拿不到
  ToolRuntime —— 想绕过协调器在这里做不到.
- **一次只派发一个工具调用.** 模型一口气要了三个工具时, 后两个排队等前一个的
  observation. 并行需要知道冲突域 (ADR-0004 §10), 而那是安全层的知识, 循环没有.
- **工具目录来自 LoopInput.** 循环不自己算"哪些工具可见", 那是 mode 能力门的事;
  它只负责把目录翻译成模型能看懂的 schema.

usage 草稿经只读属性 usage_drafts 交回驱动方; 流式增量经运行事件外送.
取消依赖 ModelRequest.cancel_token 的协作检查, 中断的流按 §9 收尾块归一为 LoopStop.

运行事件按 ADR-0016 发布到 AgentRunEventBus. 分工: 循环拥有 turn 生命周期, 步骤切换,
模型调用和"模型请求了哪个工具" (TOOL_QUEUED); 工具真正被裁决与执行之后的事件由
ToolRequestCoordinator 发布 —— 循环看不见裁决与执行, 编不出那些事实.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from forgecli.application.agent_loop.loop import AgentLoop
from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.llm.error_hints import actionable_message
from forgecli.application.llm.gateway.errors import (
    ModelBadRequestError,
    ModelCancelledError,
    ModelGatewayError,
    ModelResponseParseError,
)
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.streaming import StreamAccumulator
from forgecli.application.llm.metering import UsageMeter
from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopDecision,
    LoopObservation,
    LoopStepResult,
    LoopStop,
    ObservationDisposition,
    ToolRequest,
    ToolRequestAction,
)
from forgecli.domain.agent.prompt import PromptSnapshot
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    DecisionSummaryPayload,
    ModelCompletedPayload,
    ModelFailedPayload,
    ModelStartedPayload,
    ModelUsagePayload,
    ReasoningStatus,
    ReasoningStatusPayload,
    RunEventPayload,
    RunPhase,
    StepStartedPayload,
    TextDeltaPayload,
    ToolQueuedPayload,
    TurnFinishedPayload,
    TurnStartedPayload,
)
from forgecli.domain.agent.state import LoopBudgets, LoopInput
from forgecli.domain.agent.stop import LoopStopReason, StopClassification
from forgecli.domain.conversation.message import (
    ChatMessage,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams
from forgecli.domain.model.request import ModelRequest
from forgecli.domain.model.response import (
    FinishReason,
    ModelResponse,
    ModelUsage,
)
from forgecli.domain.model.selection import CurrentModelSelection
from forgecli.domain.model.streaming import ModelStreamChunk
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.tool_call import ToolCall, ToolSchema
from forgecli.shared.cancellation import CancelToken

# 单轮内的兜底上限. LoopBudgets 没给预算时用它, 防止模型与工具互相喂招停不下来.
_DEFAULT_MAX_MODEL_CALLS = 9999
_DEFAULT_MAX_TOOL_CALLS = 9999

# 同一个工具 + 同一份参数在一轮里允许重复几次.
#
# 这道闸是给"模型卡住"准备的, 与总预算是两回事: 总预算拦的是"活干得多", 它拦的是
# "同一件事重复做". 见过 fs.list_files 用相同参数被连着调上百次 —— 每次结果都一样,
# 模型却读不出该换个做法, 总预算再大也只是让它多转几百圈.
#
# 允许 2 次而不是 1 次: 中间穿插过写操作时, 重列一次目录是合理的.
_MAX_IDENTICAL_CALLS = 2

# 本轮累计被安全策略拒绝多少次之后就不再派工具.
#
# 数**累计**不数连续: 连续计数会被一次成功的 fs.read_file 重置, 模型只要在两次被拒之间
# 插一个无害读取, 计数器就永远回不到上限.
#
# 与 _MAX_IDENTICAL_CALLS 不重叠: 那道闸拦"同一工具同一参数", 而 `rm -rf build/` 换成
# `find build -delete` 是两个签名不同的调用, 它放行. 这里拦的正是这种换着花样撞墙.
_MAX_BLOCKED_CALLS = 3

# 停止原因分类 -> turn 终态事件. 可恢复暂停 (等审批 / 等输入) 也算"这一轮结束了",
# 终端要收掉活动区; 它与失败的区别由 TurnFinishedPayload.status 表达.
_TURN_END_KINDS: dict[StopClassification, AgentRunEventKind] = {
    StopClassification.NORMAL: AgentRunEventKind.TURN_COMPLETED,
    StopClassification.RESUMABLE_PAUSE: AgentRunEventKind.TURN_COMPLETED,
    StopClassification.POLICY_DEPENDENT: AgentRunEventKind.TURN_COMPLETED,
    StopClassification.BLOCKING: AgentRunEventKind.TURN_FAILED,
}


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


_HALT_NOTICE = (
    "本轮工具调用已停止: 上一次请求未获授权. "
    "不要尝试用别的命令或工具达成同一目的 —— 那是在绕过刚才那个决定. "
    "请说明你原本想做什么以及为什么, 然后等待用户的下一步指示."
)

_HALT_STOP_MESSAGE = "上一次请求未获授权, 本轮已停止执行工具。"

_BLOCKED_NOTICE = (
    "本轮工具调用已停止: 已被安全策略拒绝 {count} 次. "
    "继续换写法只会继续被拒. 请说明你想达成什么, 以及被挡住的是哪一步, "
    "然后等待用户指示."
)


def _labelled(call: ToolCall, content: str) -> str:
    """给回填内容加一行调用标签.

    协议层靠 tool_call_id 关联, 但模型是**读**上下文的. 一段几百行的裸文件列表和另一段
    长得一模一样, 模型认不出哪段对应哪次调用, 于是"再列一次看看" —— 这是重复调用最主要
    的来源. 标签让每段结果自带身份.
    """
    return f"{_render_call(call)} ->\n{content}"


def _render_call(call: ToolCall) -> str:
    arguments = ", ".join(
        f"{name}={value!r}" for name, value in sorted(call.arguments.items())
    )
    return f"{call.name}({arguments})"


def _signature_of(call: ToolCall) -> str:
    """一次调用的身份: 工具名 + 规范化参数. 参数顺序不同不算不同的调用."""
    return repr(
        (call.name, sorted((str(k), repr(v)) for k, v in call.arguments.items()))
    )


@dataclass(frozen=True)
class _ModelOutcome:
    """一次模型调用的归一化产出: 文本与工具调用可以同时存在."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.text.strip() and not self.tool_calls


class BuiltinAgentLoop(AgentLoop):
    """受控 ReAct 循环: 每 turn 一个新实例 (由装配方以工厂创建)."""

    def __init__(
        self,
        gateway: LlmGateway,
        usage_meter: UsageMeter,
        *,
        request_id_factory: Callable[[], str] = _new_request_id,
        cancel_token_factory: Callable[[], CancelToken | None] = lambda: None,
        event_bus: AgentRunEventBus | None = None,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gateway = gateway
        self._meter = usage_meter
        self._new_request_id = request_id_factory
        self._new_cancel_token = cancel_token_factory
        self._bus = event_bus
        self._timer = timer
        self._started = False
        self._finished = False
        self._turn_id: str | None = None
        self._usage_drafts: list[UsageRecordDraft] = []
        self._partial_answer: str | None = None
        # 本轮的运行 transcript: 每次模型调用都基于它, 工具结果按 §10 写回这里.
        self._messages: tuple[ChatMessage, ...] = ()
        # start() 之前为 None. 循环不自己造提示词 —— 编译是 application 的事.
        self._prompt: PromptSnapshot | None = None
        self._session_id = ""
        self._tools: tuple[ToolSchema, ...] = ()
        self._budgets = LoopBudgets()
        # 模型一次要了多个工具时的等待队列, 以及正在等 observation 的那一个.
        self._pending_calls: list[ToolCall] = []
        self._dispatched: ToolCall | None = None
        # (工具名 + 参数) -> 本轮已派发次数, 用于挡住原地打转.
        self._call_counts: dict[str, int] = {}
        self._blocked_calls = 0
        self._tools_closed = False
        self._model_calls = 0
        self._tool_calls = 0
        # 步骤序号: 每次"调模型 / 派工具 / 出回答"算一步, 进事件信封供终端分块.
        self._step_index = 0
        self._turn_started_at = 0.0

    # ---- AgentLoop 端口 ----

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        if self._started:
            raise RuntimeError("BuiltinAgentLoop 每轮只能 start 一次 (每 turn 新实例)")
        self._started = True
        self._turn_id = loop_input.turn_id
        self._session_id = loop_input.session_id
        self._messages = loop_input.context_package.messages
        # 本轮所有模型调用复用同一份提示词. 不在工具调用之间隐式换 (ADR-0018 §6.2):
        # 换了之后"模型为什么突然改了行为"就再也对不上任何一条事件.
        self._prompt = loop_input.context_package.prompt
        self._budgets = loop_input.budgets
        self._tools = _schemas_of(loop_input.tool_catalog)
        self._turn_started_at = self._timer()
        self._publish(
            AgentRunEventKind.TURN_STARTED,
            TurnStartedPayload(mode=loop_input.mode.value, tool_count=len(self._tools)),
        )
        return self._advance()

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        if not self._started:
            raise RuntimeError("BuiltinAgentLoop 未 start, 不接受 observe")
        if self._finished:
            return self._stop(LoopStopReason.FINAL_ANSWER, None)
        if self._dispatched is None:
            # 上一步产出的是 AnswerAction: 驱动方确认送达后本轮就结束.
            self._finished = True
            return self._stop(LoopStopReason.FINAL_ANSWER, None)
        return self._observe_tool(observation)

    # ---- 驱动方读取 (不在冻结 ABC 上, 经 getattr 鸭子类型消费) ----

    @property
    def usage_drafts(self) -> tuple[UsageRecordDraft, ...]:
        """本轮产生的 usage 草稿 (落盘由 AgentTurnService 执行)."""
        return tuple(self._usage_drafts)

    @property
    def partial_answer(self) -> str | None:
        """流式中断时已累积的部分回答文本 (无则为 None)."""
        return self._partial_answer

    # ---- 循环推进 ----

    def _advance(self) -> LoopStepResult:
        """调一次模型, 把产出翻译成动作."""
        over_budget = self._check_model_budget()
        if over_budget is not None:
            return over_budget

        self._step_index += 1
        self._publish(
            AgentRunEventKind.STEP_STARTED,
            StepStartedPayload(step_index=self._step_index, phase=RunPhase.THINKING),
        )
        request = self._build_request()
        self._publish(
            AgentRunEventKind.MODEL_STARTED,
            ModelStartedPayload(call_index=self._model_calls),
            request_id=request.request_id,
        )
        # thinking 是否有可展示内容由供应商决定; 这里只如实报"开始想了".
        # 不可用时终端显示状态即可, 绝不从回答或 token 数倒推思维链 (ADR-0016 §6).
        self._publish(
            AgentRunEventKind.MODEL_REASONING_STATUS,
            ReasoningStatusPayload(status=ReasoningStatus.STARTED),
            request_id=request.request_id,
        )
        try:
            outcome = self._call_model(request)
        except ModelCancelledError as exc:
            return self._stop(LoopStopReason.USER_CANCELLED, actionable_message(exc))
        except ModelResponseParseError as exc:
            # 工具调用参数不是完整合法 JSON: 绝不猜一个补上去, 那等于替模型编参数.
            return self._stop(LoopStopReason.MODEL_ERROR_BLOCKING, str(exc))
        except ModelGatewayError as exc:
            return self._stop(
                LoopStopReason.MODEL_ERROR_BLOCKING, actionable_message(exc)
            )
        if isinstance(outcome, LoopStop):
            # _call_stream 已经自己收过尾 (中断 / 取消), 这里只补 turn 终态.
            self._publish_turn_finished(outcome.reason)
            return outcome
        if outcome.empty:
            return self._stop(
                LoopStopReason.MODEL_ERROR_BLOCKING, "模型既没有回复也没有请求工具。"
            )

        if self._tools_closed and outcome.tool_calls:
            # 目录已经收掉了, 模型还在要工具. 靠"没给你看你就不会要"不算强制 —— 真正
            # 的强制是这里不派发. 这些 tool_calls 也不写进 transcript: 写进去就欠一份
            # 配对的 tool result, 而本轮不会再有执行了.
            if not outcome.text.strip():
                return self._stop(LoopStopReason.POLICY_DENIED, _HALT_STOP_MESSAGE)
            outcome = _ModelOutcome(text=outcome.text)

        self._remember_assistant(outcome)
        if outcome.tool_calls:
            self._pending_calls = list(outcome.tool_calls)
            return self._dispatch_next(
                reason=f"模型请求 {len(outcome.tool_calls)} 个工具调用"
            )
        self._dispatched = None
        self._step_index += 1
        self._publish(
            AgentRunEventKind.STEP_STARTED,
            StepStartedPayload(step_index=self._step_index, phase=RunPhase.ANSWER),
            request_id=request.request_id,
        )
        return self._decision("模型已产出最终回答", AnswerAction(text=outcome.text))

    def _dispatch_next(self, *, reason: str) -> LoopStepResult:
        """派发队列里的下一个工具调用. 一次一个, 不并行."""
        over_budget = self._check_tool_budget()
        if over_budget is not None:
            return over_budget
        call = self._pending_calls.pop(0)
        signature = _signature_of(call)
        self._call_counts[signature] = self._call_counts.get(signature, 0) + 1
        if self._call_counts[signature] > _MAX_IDENTICAL_CALLS:
            return self._reject_repeat(call)
        self._dispatched = call
        self._tool_calls += 1
        self._step_index += 1
        self._publish(
            AgentRunEventKind.STEP_STARTED,
            StepStartedPayload(step_index=self._step_index, phase=RunPhase.TOOL),
        )
        # 只报"模型请求了这个工具". 裁决与执行的事实由 ToolRequestCoordinator 发布 ——
        # 循环还没看到它们, 在这里编就是猜.
        self._publish(
            AgentRunEventKind.TOOL_QUEUED,
            ToolQueuedPayload(
                tool_name=call.name, queue_position=len(self._pending_calls)
            ),
            tool_call_id=call.tool_call_id,
        )
        return self._decision(
            reason,
            ToolRequestAction(
                request=ToolRequest(
                    name=call.name,
                    arguments=call.arguments,
                    tool_call_id=call.tool_call_id,
                )
            ),
        )

    def _reject_repeat(self, call: ToolCall) -> LoopStepResult:
        """同样的调用已经做过了: 不再执行, 直接把这个事实回填给模型.

        回填而不是静默跳过, 也不是直接停止本轮: 模型必须知道"你在重复", 否则它只会
        原样再要一次. 这条消息进 transcript, 下一次模型调用就看得到.
        """
        self._messages = (
            *self._messages,
            ChatMessage(
                role=MessageRole.TOOL,
                content=(
                    ToolResultBlock(
                        tool_call_id=call.tool_call_id,
                        content=(
                            f"重复调用已被拦截: {_render_call(call)} 在本轮已经调用过 "
                            f"{_MAX_IDENTICAL_CALLS} 次, 参数完全相同, 结果不会变. "
                            "请换一个做法, 或者基于已有结果直接作答."
                        ),
                        is_error=True,
                    ),
                ),
            ),
        )
        self._dispatched = None
        if self._pending_calls:
            return self._dispatch_next(reason="跳过重复调用, 派发下一个")
        return self._advance()

    def _observe_tool(self, observation: LoopObservation) -> LoopStepResult:
        """把工具结论写回 transcript, 然后继续派发或再调一次模型.

        无论结果是成功, 被策略拒绝还是待审批, 都以 tool result 的形式回填 —— 模型必须
        知道它请求的动作到底怎么了, 否则只会原样再要一次.
        """
        call = self._dispatched
        assert call is not None
        self._dispatched = None
        self._messages = (
            *self._messages,
            ChatMessage(
                role=MessageRole.TOOL,
                content=(
                    ToolResultBlock(
                        tool_call_id=call.tool_call_id,
                        content=_labelled(call, observation.content),
                        is_error=observation.is_error,
                    ),
                ),
            ),
        )
        # 这里不发 TOOL_COMPLETED: 执行结果的权威事实在协调器那边 (状态, 耗时, 退出码,
        # 是否产生了副作用). 循环只拿到一段回填文本, 用它冒充执行结论会让终端显示的
        # "完成"与真正发生的事脱节.
        halt = self._weigh(observation)
        if halt is not None:
            return self._close_tools(halt)
        if self._pending_calls:
            return self._dispatch_next(reason="继续派发同一批中的下一个工具调用")
        return self._advance()

    def _weigh(self, observation: LoopObservation) -> str | None:
        """按观察的处置意见决定要不要收掉本轮的工具. 返回收摊理由, None 表示继续.

        人类拒绝立即收: 他拒绝的是**意图**, 不是那一条命令. 允许换个说法重试, 等于让
        模型绕过人刚做的决定 —— 足够执着的模型总能绕过去, 而用户还以为自己有否决权.
        这是本轮收, 不是永久禁: 下一句话该由人说, 他可以纠正也可以换个要求.
        """
        if observation.disposition is ObservationDisposition.HALT:
            return _HALT_NOTICE
        if observation.disposition is ObservationDisposition.BLOCKED:
            self._blocked_calls += 1
            if self._blocked_calls >= _MAX_BLOCKED_CALLS:
                return _BLOCKED_NOTICE.format(count=self._blocked_calls)
        return None

    def _close_tools(self, notice: str) -> LoopStepResult:
        """收掉本轮的工具目录, 逼模型给出最终回答.

        不直接 LoopStop: 人有权知道模型原本想做什么, 以及为什么停下. 硬中止的话屏幕上
        只会突然什么都没有.
        """
        self._abandon_pending(notice)
        self._tools = ()
        self._tools_closed = True
        self._pending_calls = []
        self._dispatched = None
        self._messages = (
            *self._messages,
            ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),)),
        )
        self._publish(
            AgentRunEventKind.DECISION_SUMMARY,
            DecisionSummaryPayload(reason_summary=notice),
        )
        return self._advance()

    def _abandon_pending(self, notice: str) -> None:
        """给还在排队的调用补上 tool result.

        协议要求 assistant 消息里每个 tool_call 都有对应的 tool result: 直接丢掉它们,
        下一次请求就是残缺的, 供应商会拒。所以是"标记为未执行", 不是"当作没发生过".
        """
        for call in self._pending_calls:
            self._messages = (
                *self._messages,
                ChatMessage(
                    role=MessageRole.TOOL,
                    content=(
                        ToolResultBlock(
                            tool_call_id=call.tool_call_id,
                            content=f"未执行: {notice}",
                            is_error=True,
                        ),
                    ),
                ),
            )

    # ---- 预算 ----

    def _check_model_budget(self) -> LoopStop | None:
        limit = self._budgets.max_model_calls_per_turn or _DEFAULT_MAX_MODEL_CALLS
        if self._model_calls >= limit:
            return self._stop(
                LoopStopReason.BUDGET_EXHAUSTED,
                f"本轮模型调用已达上限 {limit}。",
            )
        return None

    def _check_tool_budget(self) -> LoopStop | None:
        limit = self._budgets.max_tool_calls_per_turn or _DEFAULT_MAX_TOOL_CALLS
        if self._tool_calls >= limit:
            return self._stop(
                LoopStopReason.BUDGET_EXHAUSTED,
                f"本轮工具调用已达上限 {limit}。",
            )
        return None

    # ---- transcript ----

    def _remember_assistant(self, outcome: _ModelOutcome) -> None:
        """把模型这一步写进 transcript.

        带 tool_calls 的 assistant 消息即使没有文本也必须留下: 后面的 tool result 要靠
        tool_call_id 关联到它 (§10), 丢了这条消息, 供应商侧就对不上号.
        """
        content = (TextBlock(outcome.text),) if outcome.text else ()
        self._messages = (
            *self._messages,
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=content,
                tool_calls=outcome.tool_calls,
            ),
        )

    # ---- 模型调用 ----

    def _build_request(self) -> ModelRequest:
        self._model_calls += 1
        return ModelRequest(
            request_id=self._new_request_id(),
            session_id=self._session_id,
            turn_id=self._turn_id or "",
            origin=(
                # 带工具目录的调用属于 act 阶段; 纯对话仍记 chat (ADR-0011 §3.3).
                RequestOrigin.ACT if self._tools else RequestOrigin.CHAT
            ),
            model_selection=CurrentModelSelection(),
            messages=self._messages,
            params=ModelParams(),
            system_prompt=self._prompt.text if self._prompt is not None else None,
            tools=self._tools,
            # 不设 cache_hint: 当前唯一的 adapter 是 OpenAI-compatible, 它走自动前缀
            # 缓存, 标不标一样. 而 CacheHint 的两个布尔也表达不了"断点在第几块之后" ——
            # 那正是 PromptSnapshot 里已经有的信息. 等接 Anthropic 这类需要显式
            # cache_control breakpoint 的 adapter 时, 按真实需要设计字段形状再接线,
            # 比现在摆一个没人读的字段强.
            cancel_token=self._new_cancel_token(),
        )

    def _call_model(self, request: ModelRequest) -> _ModelOutcome | LoopStop:
        """有人在看就走流式.

        判据是有没有事件总线: 增量只经运行事件外送 (ADR-0016 §5). 没有消费方时流式
        没有意义, 直接走非流式少一次协议开销.
        """
        if self._bus is None:
            return self._call_complete(request)
        return self._call_stream(request)

    def _call_complete(self, request: ModelRequest) -> _ModelOutcome:
        started = self._timer()
        response = self._gateway.complete(request)
        self._usage_drafts.append(self._meter.build_draft(request, response))
        # 非流式: 整段文本一次性交出, 仍然走 delta 通道, 免得终端为两种形态各写一套.
        if response.content:
            self._publish(
                AgentRunEventKind.MODEL_OUTPUT_DELTA,
                TextDeltaPayload(text=response.content),
                request_id=request.request_id,
            )
        self._finish_model_call(
            request,
            finish_reason=response.finish_reason,
            text_chars=len(response.content),
            tool_call_count=len(response.tool_calls),
            elapsed_ms=(self._timer() - started) * 1000.0,
            usage=response.usage,
        )
        return _ModelOutcome(text=response.content, tool_calls=response.tool_calls)

    def _call_stream(self, request: ModelRequest) -> _ModelOutcome | LoopStop:
        started = self._timer()
        try:
            chunks = self._gateway.stream(request)
        except ModelBadRequestError:
            # provider 不具备流式能力 (gateway 在产出首块前即拒绝): 回退非流式.
            return self._call_complete(request)
        accumulator = StreamAccumulator()
        tail: ModelStreamChunk | None = None
        for chunk in chunks:
            accumulator.add(chunk)
            if chunk.delta_text:
                self._publish(
                    AgentRunEventKind.MODEL_OUTPUT_DELTA,
                    TextDeltaPayload(text=chunk.delta_text),
                    request_id=request.request_id,
                )
            tail = chunk
        elapsed_ms = (self._timer() - started) * 1000.0
        self._partial_answer = accumulator.text or None
        self._record_stream_draft(request, tail, accumulator, elapsed_ms)
        if tail is not None and tail.interrupted:
            # 中断也要收尾这次调用: 终端得知道当前这个 request 块已经关掉了, 否则活动区
            # 会停在"正在思考"上等一个永远不来的结束事件 (ADR-0016 §5).
            if tail.finish_reason is FinishReason.USER_CANCELLED:
                self._fail_model_call(
                    request, "user_cancelled", "本轮回复已取消。", retryable=True
                )
                return LoopStop.of(
                    LoopStopReason.USER_CANCELLED, message="本轮回复已取消。"
                )
            self._fail_model_call(
                request, "stream_interrupted", "流式响应中断，本轮回复失败。"
            )
            return LoopStop.of(
                LoopStopReason.MODEL_ERROR_BLOCKING,
                message="流式响应中断，本轮回复失败。",
            )
        if accumulator.has_partial_tool_calls():
            # 半截的 tool call delta: 流没断但参数不完整, 补全它等于替模型编参数.
            self._fail_model_call(
                request, "partial_tool_call", "工具调用参数不完整，本轮中止。"
            )
            return LoopStop.of(
                LoopStopReason.MODEL_ERROR_BLOCKING,
                message="工具调用参数不完整，本轮中止。",
            )
        tool_calls = accumulator.tool_calls()
        self._finish_model_call(
            request,
            finish_reason=accumulator.finish_reason or FinishReason.STOP,
            text_chars=len(accumulator.text),
            tool_call_count=len(tool_calls),
            elapsed_ms=elapsed_ms,
            usage=accumulator.usage,
        )
        return _ModelOutcome(text=accumulator.text, tool_calls=tool_calls)

    def _record_stream_draft(
        self,
        request: ModelRequest,
        tail: ModelStreamChunk | None,
        accumulator: StreamAccumulator,
        elapsed_ms: float,
    ) -> None:
        """由流式收尾块合成 ModelResponse 复用计量; 取消/中断也记录估算用量 (§9)."""
        if tail is None or accumulator.usage is None:
            return
        response = ModelResponse(
            request_id=request.request_id,
            provider=tail.provider,
            model=tail.model,
            content=accumulator.text,
            finish_reason=accumulator.finish_reason or FinishReason.STOP,
            usage=accumulator.usage,
            latency_ms=elapsed_ms,
        )
        self._usage_drafts.append(self._meter.build_draft(request, response))

    def _stop(self, reason: LoopStopReason, message: str | None) -> LoopStop:
        self._finished = True
        self._publish_turn_finished(reason, detail=message or "")
        return LoopStop.of(reason, message=message)

    # ---- 运行事件 ----

    def _decision(self, reason_summary: str, action: object) -> LoopDecision:
        """产出决策的同时把它的理由摘要发出去.

        DECISION_SUMMARY 是 ForgeCLI 自己能解释的行动摘要, 与供应商 reasoning 分开命名:
        混成一栏, 用户会以为看到的是模型在想什么 (ADR-0016 §6).
        """
        self._publish(
            AgentRunEventKind.DECISION_SUMMARY,
            DecisionSummaryPayload(reason_summary=reason_summary),
            step_index=self._step_index,
        )
        return LoopDecision(reason_summary=reason_summary, next_action=action)  # type: ignore[arg-type]

    def _finish_model_call(
        self,
        request: ModelRequest,
        *,
        finish_reason: FinishReason,
        text_chars: int,
        tool_call_count: int,
        elapsed_ms: float,
        usage: ModelUsage | None,
    ) -> None:
        self._publish(
            AgentRunEventKind.MODEL_REASONING_STATUS,
            ReasoningStatusPayload(status=ReasoningStatus.COMPLETED),
            request_id=request.request_id,
        )
        self._publish(
            AgentRunEventKind.MODEL_COMPLETED,
            ModelCompletedPayload(
                finish_reason=finish_reason.value,
                text_chars=text_chars,
                tool_call_count=tool_call_count,
                elapsed_ms=elapsed_ms,
            ),
            request_id=request.request_id,
        )
        if usage is not None:
            self._publish(
                AgentRunEventKind.MODEL_USAGE,
                ModelUsagePayload(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    reasoning_tokens=usage.reasoning_tokens or 0,
                    cached_tokens=usage.cached_input_tokens or 0,
                    total_tokens=usage.total_tokens or 0,
                    estimated=usage.estimated,
                ),
                request_id=request.request_id,
            )

    def _fail_model_call(
        self,
        request: ModelRequest,
        error_kind: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        self._publish(
            AgentRunEventKind.MODEL_FAILED,
            ModelFailedPayload(
                error_kind=error_kind, message=message, retryable=retryable
            ),
            request_id=request.request_id,
        )

    def _publish_turn_finished(
        self, reason: LoopStopReason, *, detail: str = ""
    ) -> None:
        kind = _TURN_END_KINDS.get(reason.classification, AgentRunEventKind.TURN_FAILED)
        if reason is LoopStopReason.USER_CANCELLED:
            # USER_CANCELLED 归类是 BLOCKING, 但对用户来说它是"我按了 Ctrl-C", 不是故障.
            kind = AgentRunEventKind.TURN_CANCELLED
        self._publish(
            kind,
            TurnFinishedPayload(
                status=reason.value,
                elapsed_ms=(self._timer() - self._turn_started_at) * 1000.0,
                model_calls=self._model_calls,
                tool_calls=self._tool_calls,
                detail=detail,
            ),
        )

    def _publish(
        self,
        kind: AgentRunEventKind,
        payload: RunEventPayload,
        *,
        step_index: int | None = None,
        request_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        if self._bus is None or self._turn_id is None:
            return
        self._bus.publish(
            kind,
            turn_id=self._turn_id,
            payload=payload,
            step_index=step_index if step_index is not None else self._step_index,
            request_id=request_id,
            tool_call_id=tool_call_id,
        )


def _schemas_of(catalog: ToolCatalog | None) -> tuple[ToolSchema, ...]:
    """把目录翻译成模型侧 schema. 目录为空表示本档不暴露任何工具."""
    return () if catalog is None else catalog.to_model_schemas()
