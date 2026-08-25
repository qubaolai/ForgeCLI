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

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_run.scrubbing import scrub_arguments
from forgecli.application.context.manager import ContextManager
from forgecli.application.llm.error_hints import actionable_message
from forgecli.application.llm.gateway.errors import (
    MalformedToolCallError,
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
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.context.compaction import CompactionDraft
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
from forgecli.domain.prompt import text as prompt_text
from forgecli.domain.prompt.blocks import PromptSnapshot
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.tool_call import ToolCall, ToolSchema
from forgecli.shared.cancellation import CancelToken
from forgecli.shared.observability.context import update as update_run_context
from forgecli.shared.observability.log import get_log

_log = get_log(__name__)

# 单轮内的兜底上限. LoopBudgets 没给预算时用它, 防止模型与工具互相喂招停不下来.
_DEFAULT_MAX_MODEL_CALLS = 9999
_DEFAULT_MAX_TOOL_CALLS = 9999

# 同一个工具 + 同一份参数在一轮里允许重复几次.
#
# 这道闸是给"模型卡住"准备的, 与总预算是两回事: 总预算拦的是"活干得多", 它拦的是
# "同一件事重复做". 见过 fs_find 用相同参数被连着调上百次 —— 每次结果都一样,
# 模型却读不出该换个做法, 总预算再大也只是让它多转几百圈.
#
# 允许 2 次而不是 1 次: 中间穿插过写操作时, 重列一次目录是合理的.
#
# 2026-08-19 重新评估过降到 1, 结论是不降. 上面那条"写完再列一次"是真实场景, 而循环判断
# 不了"中间那次调用改没改工作区" —— 那是安全层的知识, 拿进来等于让循环认识工具语义.
# 真正的漏网之鱼是**参数每次都变**的原地打转 (8 个 grep 变体各不相同), 它按签名判身份
# 本来就拦不住, 该由 _MAX_BARREN_OBSERVATIONS 接手.
_MAX_IDENTICAL_CALLS = 2

# 本轮累计被安全策略拒绝多少次之后就不再派工具.
#
# 数**累计**不数连续: 连续计数会被一次成功的 fs_read_file 重置, 模型只要在两次被拒之间
# 插一个无害读取, 计数器就永远回不到上限.
#
# 与 _MAX_IDENTICAL_CALLS 不重叠: 那道闸拦"同一工具同一参数", 而 `rm -rf build/` 换成
# `find build -delete` 是两个签名不同的调用, 它放行. 这里拦的正是这种换着花样撞墙.
_MAX_BLOCKED_CALLS = 3

# 本轮累计收到多少次不可用的工具调用之后放弃.
#
# "不可用"指模型自己的输出坏了: 参数不是完整 JSON, 或参数里混进了工具调用 markup.
# 见过一次真实任务里 search_text 的唯一一次调用就这么被打掉, 而模型收不到任何反馈,
# 从此再没碰过这个工具, 全程改用 shell_run —— 一次静默的格式失败足以让一个工具从模型
# 的选项里永久消失.
#
# 与另外两道闸的分工: _MAX_IDENTICAL_CALLS 拦"同一件事重复做", _MAX_BLOCKED_CALLS 拦
# "换着花样撞安全策略", 这一道拦的是"话都说不利索". 前两道的输入是合法调用, 这一道的
# 输入根本不是.
#
# 数**累计**不数连续, 理由同 _MAX_BLOCKED_CALLS: 中间夹一次成功调用就重置的话, 一个
# 每隔一步坏一次的模型能把整轮预算烧光而永远撞不到上限.
_MAX_MALFORMED_RESPONSES = 2

# 连续多少次工具调用没带回新信息之后提醒一次.
#
# 这道闸补的是 _MAX_IDENTICAL_CALLS 的盲区: 那道闸按**入参**判身份, 而一次真实任务里
# 8 个 grep 变体的参数各不相同 (加个 --include, 加个 | head, 换个转义), 全部放行, 返回
# 的却都是同一个空结果. 真正的浪费信号不是"参数一样", 是"结果没告诉我新东西".
#
# 只提醒, 不收工具: 空结果不是错误, 模型该做的是换个思路或者直接报告"没找到", 而这两件
# 事都还需要工具. 收掉目录等于因为没找到就罚它闭嘴.
_MAX_BARREN_OBSERVATIONS = 3

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


# 模型侧工具调用 markup. 它出现在**参数值或工具名**里, 说明供应商没能把模型的工具调用
# 解析干净, 把标记连同内容一起塞进了参数.
#
# 这类调用不能当正常参数派发下去: 它会一路走到安全裁决, 拿回 executable_not_found 之类
# 的结论, 而那个结论会把模型引向"我命令写错了" —— 真正坏掉的是它的输出格式, 照着错误
# 的结论改只会一直错下去.
_PROTOCOL_MARKUP = (
    "<tool_call>",
    "</tool_call>",
    "<arg_key>",
    "<arg_value>",
    "<think>",
    "</think>",
)


def _protocol_markup_in(call: ToolCall) -> str | None:
    """调用里混进的第一个 markup 标记; 干净则返回 None.

    只看工具名与**字符串**参数值: 结构化的嵌套值不会承载这类泄漏, 而把整个参数字典
    序列化去搜会把正常的代码内容误判成 markup —— 模型完全可能在写一段含 `<think>`
    的 HTML.
    """
    for marker in _PROTOCOL_MARKUP:
        if marker in call.name:
            return marker
        for value in call.arguments.values():
            if isinstance(value, str) and marker in value:
                return marker
    return None


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


def _transcript_view(messages: tuple[ChatMessage, ...]) -> list[dict[str, object]]:
    """把 transcript 摊平成可以直接读的形状, 供 debug 级日志写出整段上下文.

    排查"模型为什么突然这么答"时, 真正要看的就是那一刻发给它的完整消息序列 —— 事件
    日志里只有落盘的成对 user / assistant, 轮内的 tool result 回填与压缩改写不在那里.
    只在 DEBUG 下调用, info 级别不会为它拼这个字符串.
    """
    view: list[dict[str, object]] = []
    for message in messages:
        item: dict[str, object] = {"role": message.role.value}
        texts = [
            block.text for block in message.content if isinstance(block, TextBlock)
        ]
        if texts:
            item["text"] = "\n".join(texts)
        results = [
            {
                "tool_call_id": block.tool_call_id,
                "is_error": block.is_error,
                "content": block.content,
            }
            for block in message.content
            if isinstance(block, ToolResultBlock)
        ]
        if results:
            item["tool_results"] = results
        if message.tool_calls:
            item["tool_calls"] = [
                {"id": call.tool_call_id, "name": call.name, "args": call.arguments}
                for call in message.tool_calls
            ]
        view.append(item)
    return view


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


class BuiltinAgentLoop:
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
        context: ContextManager | None = None,
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
        # 压缩记录 (ADR-0032 决策 1). 循环攒着, AgentTurnService 落盘 —— 与
        # usage_drafts 同一条分工: 循环可以调模型, 但不写事件 (ADR-0010).
        self._compaction_drafts: list[CompactionDraft] = []
        self._partial_answer: str | None = None
        # 本轮的运行 transcript: 每次模型调用都基于它, 工具结果按 §10 写回这里.
        self._messages: tuple[ChatMessage, ...] = ()
        # start() 之前为 None. 循环不自己造提示词 —— 编译是 application 的事.
        self._prompt: PromptSnapshot | None = None
        self._session_id = ""
        self._tools: tuple[ToolSchema, ...] = ()
        self._budgets = LoopBudgets()
        # 缺省为 None: 没给预算就不压缩, 与接入之前的行为一致. 不猜一个窗口大小.
        self._context = context
        self._context_budget: ContextBudget | None = None
        # 模型一次要了多个工具时的等待队列, 以及正在等 observation 的那一个.
        self._pending_calls: list[ToolCall] = []
        self._dispatched: ToolCall | None = None
        # (工具名 + 参数) -> 本轮已派发次数, 用于挡住原地打转.
        self._call_counts: dict[str, int] = {}
        self._blocked_calls = 0
        self._malformed_responses = 0
        # 连续几次工具调用没带回新信息, 以及上一次带回的是什么.
        self._barren_streak = 0
        self._last_observation: str | None = None
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
        self._context_budget = loop_input.context_package.budget
        self._budgets = loop_input.budgets
        self._tools = _schemas_of(loop_input.tool_catalog)
        self._turn_started_at = self._timer()
        # 本轮的标识就位, 上一轮的清零. `update` 没有还原点, 所以由每一轮自己把
        # step / req / tool 归零 —— 否则 loop.start 那一行会带着上一轮的步号.
        update_run_context(
            session_id=self._session_id,
            turn_id=self._turn_id or "",
            step=0,
            request_id="",
            tool="",
        )
        _log.info(
            "loop.start",
            mode=loop_input.mode.value,
            tools=[schema.name for schema in self._tools],
            messages=len(self._messages),
            prompt_fingerprint=(
                "" if self._prompt is None else self._prompt.fingerprint
            ),
            max_model_calls=self._budgets.max_model_calls_per_turn,
            max_tool_calls=self._budgets.max_tool_calls_per_turn,
            context_window=(
                None
                if self._context_budget is None
                else self._context_budget.context_window
            ),
            context_allowance=(
                None if self._context_budget is None else self._context_budget.allowance
            ),
        )
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
    def compaction_drafts(self) -> tuple[CompactionDraft, ...]:
        """本轮发生过的压缩 (落盘由 AgentTurnService 执行)."""
        return tuple(self._compaction_drafts)

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

        too_long = self._fit_context()
        if too_long is not None:
            return too_long

        self._step_index += 1
        # 顺带把 tool 清掉: 这一步是在想, 不在跑工具. 不清的话上一次调用的工具名会
        # 一直挂在后面每一行上, 读日志的人会以为那几行也属于它.
        update_run_context(step=self._step_index, tool="")
        self._publish(
            AgentRunEventKind.STEP_STARTED,
            StepStartedPayload(step_index=self._step_index, phase=RunPhase.THINKING),
        )
        request = self._build_request()
        update_run_context(request_id=request.request_id)
        _log.info(
            "model.request",
            call_index=self._model_calls,
            origin=request.origin.value,
            messages=len(request.messages),
            tools=len(self._tools),
            streaming=self._bus is not None,
        )
        if _log.enabled_for_debug():
            # 先问再拼: _transcript_view 要走一遍整段 transcript, 而 DEBUG 关掉时
            # 那份结果会被原样丢掉 —— 参数是在调用之前求值的, 级别检查拦不住它.
            _log.debug(
                "model.request.messages", messages=_transcript_view(self._messages)
            )
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
            _log.warning("model.cancelled", message=str(exc))
            return self._stop(LoopStopReason.USER_CANCELLED, actionable_message(exc))
        except MalformedToolCallError as exc:
            # 绝不猜一个参数补上去, 那等于替模型编参数.
            #
            # 但"不替它补"和"不告诉它"是两件事. 只中止的话, 模型这一轮什么反馈都拿不到,
            # 下次还会原样再来一次; 告诉它坏在哪, 它自己能改. 这里只做后者.
            return self._retry_malformed(str(exc))
        except ModelResponseParseError as exc:
            # 父类留给 provider 的响应压根不是 JSON 这类传输层故障: 那不是模型的错,
            # 追加纠错消息是在冤枉它, 重发同一条请求也不会好转.
            _log.exception("model.parse_failed", message=str(exc))
            return self._stop(LoopStopReason.MODEL_ERROR_BLOCKING, str(exc))
        except ModelGatewayError as exc:
            _log.exception(
                "model.gateway_failed",
                error=type(exc).__name__,
                message=str(exc),
            )
            return self._stop(
                LoopStopReason.MODEL_ERROR_BLOCKING, actionable_message(exc)
            )
        if isinstance(outcome, LoopStop):
            # _call_stream 已经自己收过尾 (中断 / 取消), 这里只补 turn 终态.
            self._publish_turn_finished(outcome.reason)
            return outcome
        if outcome.empty:
            _log.warning("model.empty_response")
            return self._stop(
                LoopStopReason.MODEL_ERROR_BLOCKING, prompt_text.EMPTY_RESPONSE_STOP
            )

        # markup 检查必须在 _remember_assistant 之前: 这批调用一个都不会派发, 把带
        # tool_calls 的 assistant 消息写进 transcript 就欠下一堆永远等不到的
        # tool result, 下一次请求会因此残缺 (§10).
        for call in outcome.tool_calls:
            marker = _protocol_markup_in(call)
            if marker is not None:
                _log.warning(
                    "model.protocol_markup",
                    tool=call.name,
                    marker=marker,
                    arguments=call.arguments,
                )
                return self._retry_malformed(
                    prompt_text.MALFORMED_DETAIL.format(tool=call.name, marker=marker)
                )

        if self._tools_closed and outcome.tool_calls:
            # 目录已经收掉了, 模型还在要工具. 靠"没给你看你就不会要"不算强制 —— 真正
            # 的强制是这里不派发. 这些 tool_calls 也不写进 transcript: 写进去就欠一份
            # 配对的 tool result, 而本轮不会再有执行了.
            _log.warning(
                "loop.tool_calls_after_close",
                requested=[call.name for call in outcome.tool_calls],
                has_text=bool(outcome.text.strip()),
            )
            if not outcome.text.strip():
                return self._stop(LoopStopReason.POLICY_DENIED, prompt_text.HALT_STOP)
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
        update_run_context(step=self._step_index, tool=call.name)
        # 入参**原样**写进日志, 与发给终端的 scrub_arguments 是两条路: 屏幕上要防的是
        # 一屏 base64 把过程刷没, 而排查一次"工具为什么这么干"必须看到它真正收到了什么.
        _log.info(
            "tool.requested",
            tool=call.name,
            tool_call_id=call.tool_call_id,
            arguments=call.arguments,
            queued=len(self._pending_calls),
            tool_call_index=self._tool_calls,
        )
        self._publish(
            AgentRunEventKind.STEP_STARTED,
            StepStartedPayload(step_index=self._step_index, phase=RunPhase.TOOL),
        )
        # 只报"模型请求了这个工具". 裁决与执行的事实由 ToolRequestCoordinator 发布 ——
        # 循环还没看到它们, 在这里编就是猜.
        self._publish(
            AgentRunEventKind.TOOL_QUEUED,
            ToolQueuedPayload(
                tool_name=call.name,
                queue_position=len(self._pending_calls),
                # 连 prepare 都走不到的调用 (工具名不存在, schema 不合法) 只有排队与
                # 终态两条事件. 入参不在这里发出去, 它在整条时间线上一次都不会出现.
                arguments=scrub_arguments(call.arguments),
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
        _log.warning(
            "loop.repeat_call_rejected",
            tool=call.name,
            arguments=call.arguments,
            limit=_MAX_IDENTICAL_CALLS,
            seen=self._call_counts[_signature_of(call)],
        )
        self._messages = (
            *self._messages,
            ChatMessage(
                role=MessageRole.TOOL,
                content=(
                    ToolResultBlock(
                        tool_call_id=call.tool_call_id,
                        content=prompt_text.REPEAT_CALL_NOTICE.format(
                            call=_render_call(call), limit=_MAX_IDENTICAL_CALLS
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
        _log.info(
            "tool.observed",
            tool=call.name,
            is_error=observation.is_error,
            disposition=observation.disposition.value,
            chars=len(observation.content),
            content=observation.content,
        )
        self._messages = (
            *self._messages,
            ChatMessage(
                role=MessageRole.TOOL,
                content=(
                    ToolResultBlock(
                        tool_call_id=call.tool_call_id,
                        content=_labelled(call, observation.content),
                        is_error=observation.is_error,
                        provenance=observation.provenance,
                    ),
                ),
            ),
        )
        # 这里不发 TOOL_COMPLETED: 执行结果的权威事实在协调器那边 (状态, 耗时, 退出码,
        # 是否产生了副作用). 循环只拿到一段回填文本, 用它冒充执行结论会让终端显示的
        # "完成"与真正发生的事脱节.
        self._track_progress(observation)
        if observation.disposition is ObservationDisposition.AWAIT_USER_DECISION:
            # 工具交出了需要人裁决的东西, 本轮到此为止 (ADR-0023 决策 1).
            #
            # 不走 _close_tools: 那条路是"工具没了但你继续说", 而这里模型已经把要说
            # 的说完了 —— 它交出了一份计划, 正等着回话. 再逼它说一段话, 只会在评审
            # 界面上方多出一段没人读的文字.
            #
            # 排队中的调用仍然补上配对的 tool result. 本轮的 transcript 到此为止,
            # 但它仍然是这一轮的完整记录, 而一份缺了配对结果的记录在任何后续消费者
            # 眼里都是残缺的.
            self._abandon_pending(prompt_text.REVIEW_ABANDON_NOTICE)
            self._pending_calls = []
            return self._stop(LoopStopReason.WAIT_PLAN_REVIEW, None)
        halt = self._weigh(observation)
        if halt is not None:
            return self._close_tools(halt)
        if self._pending_calls:
            return self._dispatch_next(reason="继续派发同一批中的下一个工具调用")
        # 提醒只在整批工具结果都回填完之后追加. 插在两条 tool result 中间会打断
        # "assistant 的 tool_calls -> 配对的 tool result"这段连续区, 供应商会拒.
        nudge = self._barren_nudge()
        if nudge is not None:
            self._messages = (
                *self._messages,
                ChatMessage(role=MessageRole.USER, content=(TextBlock(nudge),)),
            )
        return self._advance()

    def _track_progress(self, observation: LoopObservation) -> None:
        """记一次调用有没有带回新信息.

        判据两条, 满足其一就算没有: 内容为空, 或与上一次的内容完全相同.

        失败的观察不参与计数 —— 它们有专门的闸 (_MAX_BLOCKED_CALLS 与 HALT). 两个计数器
        数同一件事, 事后就说不清到底是哪条规则停的.
        """
        if observation.is_error:
            return
        content = observation.content.strip()
        if not content or content == self._last_observation:
            self._barren_streak += 1
        else:
            self._barren_streak = 0
        self._last_observation = content

    def _barren_nudge(self) -> str | None:
        """到了连续次数就给一句提醒, 并把计数清零 —— 否则之后每一步都会再提醒一次."""
        if self._barren_streak < _MAX_BARREN_OBSERVATIONS:
            return None
        count = self._barren_streak
        self._barren_streak = 0
        notice = prompt_text.BARREN_NOTICE.format(count=count)
        _log.warning("loop.barren_streak", count=count)
        self._publish(
            AgentRunEventKind.DECISION_SUMMARY,
            DecisionSummaryPayload(reason_summary=f"连续 {count} 次调用没有新信息"),
        )
        return notice

    def _weigh(self, observation: LoopObservation) -> str | None:
        """按观察的处置意见决定要不要收掉本轮的工具. 返回收摊理由, None 表示继续.

        人类拒绝立即收: 他拒绝的是**意图**, 不是那一条命令. 允许换个说法重试, 等于让
        模型绕过人刚做的决定 —— 足够执着的模型总能绕过去, 而用户还以为自己有否决权.
        这是本轮收, 不是永久禁: 下一句话该由人说, 他可以纠正也可以换个要求.
        """
        if observation.disposition is ObservationDisposition.HALT:
            _log.warning("loop.halt", reason="user_refused")
            return prompt_text.HALT_NOTICE
        if observation.disposition is ObservationDisposition.BLOCKED:
            self._blocked_calls += 1
            _log.warning(
                "loop.blocked_call",
                blocked=self._blocked_calls,
                limit=_MAX_BLOCKED_CALLS,
            )
            if self._blocked_calls >= _MAX_BLOCKED_CALLS:
                return prompt_text.BLOCKED_NOTICE.format(count=self._blocked_calls)
        return None

    def _close_tools(self, notice: str) -> LoopStepResult:
        """收掉本轮的工具目录, 逼模型给出最终回答.

        不直接 LoopStop: 人有权知道模型原本想做什么, 以及为什么停下. 硬中止的话屏幕上
        只会突然什么都没有.
        """
        _log.warning(
            "loop.tools_closed",
            notice=notice,
            abandoned=[call.name for call in self._pending_calls],
        )
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

    def _retry_malformed(self, detail: str) -> LoopStepResult:
        """模型产出了不可用的工具调用: 告诉它坏在哪, 再给一次机会.

        与 _close_tools 的形状一致 (往 transcript 追加一条 USER 通知再 _advance), 但
        **不收工具目录** —— 模型没有做错事, 只是话没说利索, 收掉目录等于因为口吃罚它
        闭嘴.

        这里不写 assistant 消息: 那次回复里没有一条可用的 tool call, 记下来只会在
        transcript 里留一批配不上 tool result 的 tool_calls.
        """
        self._malformed_responses += 1
        _log.warning(
            "model.malformed_tool_call",
            detail=detail,
            count=self._malformed_responses,
            limit=_MAX_MALFORMED_RESPONSES,
        )
        if self._malformed_responses > _MAX_MALFORMED_RESPONSES:
            return self._stop(
                LoopStopReason.MODEL_ERROR_BLOCKING,
                prompt_text.MALFORMED_STOP.format(count=self._malformed_responses),
            )
        notice = prompt_text.MALFORMED_NOTICE.format(detail=detail)
        self._messages = (
            *self._messages,
            ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),)),
        )
        self._publish(
            AgentRunEventKind.DECISION_SUMMARY,
            DecisionSummaryPayload(reason_summary=f"工具调用格式损坏, 重试: {detail}"),
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
                            content=prompt_text.ABANDONED_CALL.format(notice=notice),
                            is_error=True,
                        ),
                    ),
                ),
            )

    # ---- 预算 ----

    def _check_model_budget(self) -> LoopStop | None:
        limit = self._budgets.max_model_calls_per_turn or _DEFAULT_MAX_MODEL_CALLS
        if self._model_calls >= limit:
            _log.warning("loop.model_budget_exhausted", limit=limit)
            return self._stop(
                LoopStopReason.BUDGET_EXHAUSTED,
                prompt_text.MODEL_BUDGET_STOP.format(limit=limit),
            )
        return None

    def _check_tool_budget(self) -> LoopStop | None:
        limit = self._budgets.max_tool_calls_per_turn or _DEFAULT_MAX_TOOL_CALLS
        if self._tool_calls >= limit:
            _log.warning("loop.tool_budget_exhausted", limit=limit)
            return self._stop(
                LoopStopReason.BUDGET_EXHAUSTED,
                prompt_text.TOOL_BUDGET_STOP.format(limit=limit),
            )
        return None

    def _fit_context(self) -> LoopStop | None:
        """调模型之前整理一次上下文 (ADR-0032 决策 1).

        放在这里而不是等网关抛 ModelContextOverflowError: 那条路上请求已经组好了, 而且
        它今天是终止性的 —— 一轮跑了二十步的工作会因为最后一次调用超窗而整个作废.

        压完仍然放不下时停在 CONTEXT_COMPACTION_REQUIRED, 不把一个必然被拒的请求发出去.
        它归类为 RESUMABLE_PAUSE: 这一轮干不下去了, 但会话没坏.
        """
        if self._context is None:
            return None
        result = self._context.fit(
            self._messages,
            budget=self._context_budget,
            session_id=self._session_id,
            turn_id=self._turn_id or "",
            system_prompt="" if self._prompt is None else self._prompt.text,
            tools=self._tools,
        )
        self._messages = result.messages
        self._compaction_drafts.extend(result.drafts)
        if result.drafts:
            _log.info(
                "context.fit",
                drafts=len(result.drafts),
                tokens_saved=sum(draft.tokens_saved for draft in result.drafts),
                messages=len(result.messages),
                over_allowance=result.over_allowance,
            )
        if result.over_allowance:
            _log.error("context.over_allowance", messages=len(result.messages))
            return self._stop(
                LoopStopReason.CONTEXT_COMPACTION_REQUIRED,
                prompt_text.COMPACTION_FAILED,
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
            # 没有 cache_hint 这类字段: 当前唯一的 adapter 走自动前缀缓存, 标不标
            # 一样. 原先那个字段已按此删除, 理由见 llm/gateway/cache.py 的模块注释.
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
        _log.debug("model.response.text", text=response.content)
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
                    request,
                    "user_cancelled",
                    prompt_text.CANCELLED_STOP,
                    retryable=True,
                )
                return LoopStop.of(
                    LoopStopReason.USER_CANCELLED,
                    message=prompt_text.CANCELLED_STOP,
                )
            self._fail_model_call(
                request, "stream_interrupted", prompt_text.STREAM_INTERRUPTED_STOP
            )
            return LoopStop.of(
                LoopStopReason.MODEL_ERROR_BLOCKING,
                message=prompt_text.STREAM_INTERRUPTED_STOP,
            )
        if accumulator.has_partial_tool_calls():
            # 半截的 tool call delta: 流没断但参数不完整, 补全它等于替模型编参数.
            #
            # 这次模型调用确实失败了, 所以照常 _fail_model_call 收尾; 但本轮不一定要
            # 结束, 所以抛而不是 return —— 由 _advance 的统一处理决定重试还是中止,
            # 与非流式路径走同一条判断.
            self._fail_model_call(
                request, "partial_tool_call", prompt_text.PARTIAL_TOOL_CALL_STOP
            )
            raise MalformedToolCallError(prompt_text.PARTIAL_TOOL_CALL_STOP)
        tool_calls = accumulator.tool_calls()
        self._finish_model_call(
            request,
            finish_reason=accumulator.finish_reason or FinishReason.STOP,
            text_chars=len(accumulator.text),
            tool_call_count=len(tool_calls),
            elapsed_ms=elapsed_ms,
            usage=accumulator.usage,
        )
        _log.debug("model.response.text", text=accumulator.text)
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
        # 停下来之后的行不该还挂着本轮最后一步的标识. session / turn 由外层
        # AgentTurnService 的 bind 负责还原, 这里只清自己写进去的那几个.
        update_run_context(step=0, request_id="", tool="")
        _log.info(
            "loop.stop",
            reason=reason.value,
            message=message,
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
            steps=self._step_index,
            elapsed_ms=(self._timer() - self._turn_started_at) * 1000.0,
        )
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
        _log.info(
            "model.response",
            finish_reason=finish_reason.value,
            text_chars=text_chars,
            tool_calls=tool_call_count,
            elapsed_ms=elapsed_ms,
            input_tokens=None if usage is None else usage.input_tokens,
            output_tokens=None if usage is None else usage.output_tokens,
            cached_tokens=None if usage is None else usage.cached_input_tokens,
            reasoning_tokens=None if usage is None else usage.reasoning_tokens,
            estimated=None if usage is None else usage.estimated,
        )
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
        _log.error(
            "model.failed",
            error_kind=error_kind,
            message=message,
            retryable=retryable,
        )
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
