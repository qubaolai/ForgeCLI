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

**这个模块只剩控制流** (ADR-0048 决策 5). 三件与"下一步做什么"无关的事各自成模块:

- ``run_events.LoopEventPublisher`` 把发生的事翻译成事件, 并持有步骤序号.
- ``model_call.ModelCaller`` 发一次调用, 把流式与非流式归一成一个结果.
- ``progress.ProgressGuard`` 判断这一轮有没有在原地打转.
- ``transcript`` 是几个纯函数: 一次调用怎么写成模型读得懂的文字.

读这个文件应该只需要跟着 start -> _advance -> _dispatch_next -> _observe_tool -> _stop
这条线走; 上面四个模块各自带着自己的判据与理由, 不必同时读.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from forgecli.application.agent_loop.model_invoker import (
    AgentModelInvoker,
    ModelOutcome,
)
from forgecli.application.agent_loop.progress import ProgressGuard
from forgecli.application.agent_loop.run_events import LoopEventPublisher
from forgecli.application.agent_loop.transcript import (
    labelled,
    protocol_markup_in,
    render_call,
    transcript_view,
)
from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.context.window_manager import WindowManager
from forgecli.application.llm.error_hints import actionable_message
from forgecli.application.llm.gateway.errors import (
    MalformedToolCallError,
    ModelCancelledError,
    ModelContextOverflowError,
    ModelGatewayError,
    ModelResponseParseError,
)
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.metering import UsageMeter
from forgecli.application.llm.transport_policy import ModelTransportPolicy
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.application.workspace.monitor import (
    WorkspaceChangeMonitor,
    WorkspaceSnapshotProvider,
)
from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopAction,
    LoopObservation,
    LoopStepResult,
    LoopStop,
    ObservationDisposition,
    ToolRequest,
    ToolRequestAction,
)
from forgecli.domain.agent.state import AssembledContext, LoopInput
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.context.compaction import CompactionDraft
from forgecli.domain.context.window import Window
from forgecli.domain.conversation.message import (
    ChatMessage,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams
from forgecli.domain.model.request import ModelRequest
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.tool_call import ToolCall, ToolSchema
from forgecli.domain.workspace.changes import WorkspaceChange, WorkspaceChangeSource
from forgecli.shared.cancellation import CancelToken
from forgecli.shared.observability.context import update as update_run_context
from forgecli.shared.observability.log import get_log

_log = get_log(__name__)

# 单轮内模型调用的兜底上限, 防止模型与工具互相喂招停不下来. 工具调用本身不设总数上限.
_DEFAULT_MAX_MODEL_CALLS = 9999

# 本轮累计收到多少次不可用的工具调用之后放弃.
#
# "不可用"指模型自己的输出坏了: 参数不是完整 JSON, 或参数里混进了工具调用 markup.
# 见过一次真实任务里 search_text 的唯一一次调用就这么被打掉, 而模型收不到任何反馈,
# 从此再没碰过这个工具, 全程改用 shell_run —— 一次静默的格式失败足以让一个工具从模型
# 的选项里永久消失.
#
# 与 progress.py 那三道闸的分工: 那边拦"同一件事重复做", "换着花样撞安全策略", 以及
# "调用没带回新信息"; 这一道拦的是"话都说不利索". 那三道的输入是合法调用, 这一道的
# 输入根本不是 —— 所以它留在循环里: 走到这里连一个可派发的调用都没有.
#
# 数**累计**不数连续, 理由同 progress.MAX_BLOCKED_CALLS: 中间夹一次成功调用就重置,
# 一个
# 每隔一步坏一次的模型能把整轮预算烧光而永远撞不到上限.
_MAX_MALFORMED_RESPONSES = 2


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


class BuiltinAgentLoop:
    """受控 ReAct 循环: 每 turn 一个新实例 (由装配方以工厂创建)."""

    def __init__(
        self,
        gateway: LlmGateway,
        usage_meter: UsageMeter,
        *,
        request_id_factory: Callable[[], str] = _new_request_id,
        model_transport_policy: ModelTransportPolicy,
        cancel_token_factory: Callable[[], CancelToken | None] = lambda: None,
        event_bus: AgentRunEventBus | None = None,
        timer: Callable[[], float] = time.monotonic,
        context: WindowManager | None = None,
        workspace_snapshot_provider: WorkspaceSnapshotProvider | None = None,
    ) -> None:
        self._new_request_id = request_id_factory
        self._new_cancel_token = cancel_token_factory
        self._timer = timer
        # 三个协作者, 三种它们各自负责的事: 把发生的事翻译成运行事件, 发一次模型调用,
        # 判断这一轮有没有在原地打转。控制流留在这个类里。
        self._events = LoopEventPublisher(event_bus, timer=timer)
        self._progress = ProgressGuard()
        self._caller = AgentModelInvoker(
            gateway,
            usage_meter,
            self._events,
            # 压缩产生的草稿也进同一个列表 (ADR-0037): 分成两份迟早会出现两套单价。
            on_usage=self._usage_drafts_sink,
            timer=timer,
        )
        self._started = False
        self._finished = False
        self._transport_policy = model_transport_policy
        self._turn_id: str | None = None
        self._usage_drafts: list[UsageRecordDraft] = []
        # 压缩记录 (ADR-0032 决策 1). 循环攒着, AgentTurnService 落盘 —— 与
        # usage_drafts 同一条分工: 循环可以调模型, 但不写事件 (ADR-0010).
        self._compaction_drafts: list[CompactionDraft] = []
        # 本轮的会话窗口: 每次模型调用都基于它, 工具结果按 §10 写回这里.
        #
        # 存 Window 而不是裸元组, 是因为 `evicted_count` 必须跨调用累计 —— 淘汰要靠它
        # 判断"头一条是不是上一份交接说明". 每次现造一个 Window(messages=...) 的话那个
        # 计数永远是 0, 那条防线就永远不触发, 而它不会报错.
        self._window = Window()
        # start() 之前为 None. 循环不自己造提示词 —— 编译是 application 的事.
        self._assembled: AssembledContext | None = None
        self._session_id = ""
        self._tools: tuple[ToolSchema, ...] = ()
        # 缺省为 None: 没给预算就不压缩, 与接入之前的行为一致. 不猜一个窗口大小.
        self._context = context
        self._context_budget: ContextBudget | None = None
        self._workspace_monitor = (
            None
            if workspace_snapshot_provider is None
            else WorkspaceChangeMonitor(workspace_snapshot_provider)
        )
        self._workspace_changes: list[WorkspaceChange] = []
        # 模型一次要了多个工具时的等待队列, 以及正在等 observation 的那一个.
        self._pending_calls: list[ToolCall] = []
        self._dispatched: ToolCall | None = None
        self._malformed_responses = 0
        # 供应商说上下文太长之后强压过几次. 估算与真实分词永远有偏差, 这条路是
        # _fit_context 的下界而不是它的备份.
        self._overflow_compactions = 0
        self._tools_closed = False
        self._model_calls = 0
        self._tool_calls = 0

    def _usage_drafts_sink(self, draft: UsageRecordDraft) -> None:
        self._usage_drafts.append(draft)

    # ---- AgentLoop 端口 ----

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        if self._started:
            raise RuntimeError("BuiltinAgentLoop 每轮只能 start 一次 (每 turn 新实例)")
        self._started = True
        self._turn_id = loop_input.turn_id
        self._session_id = loop_input.session_id
        self._window = Window(messages=loop_input.context.initial_window)
        # 本轮所有模型调用复用同一份上下文前缀. 不在工具调用之间隐式换 (ADR-0018 §6.2):
        # 换了之后"模型为什么突然改了行为"就再也对不上任何一条事件.
        self._assembled = loop_input.context
        self._context_budget = loop_input.context.budget
        self._tools = _schemas_of(loop_input.tool_catalog)
        if self._workspace_monitor is not None:
            self._workspace_monitor.start()
        self._events.start_turn(session_id=self._session_id, turn_id=self._turn_id)
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
                "" if self._assembled is None else self._assembled.policy.fingerprint
            ),
            max_model_calls=_DEFAULT_MAX_MODEL_CALLS,
            max_tool_calls="unlimited",
            context_window=(
                None
                if self._context_budget is None
                else self._context_budget.context_window
            ),
            context_allowance=(
                None if self._context_budget is None else self._context_budget.allowance
            ),
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

    # ---- 驱动方读取 ----

    @property
    def _messages(self) -> tuple[ChatMessage, ...]:
        """窗口里的消息. 读多写少, 所以只读走这里, 写走 `_append` 与 `_window`."""
        return self._window.messages

    def _append(self, *messages: ChatMessage) -> None:
        """窗口只追加 (ADR-0041 决策 4). 全部写入都走这一个口子."""
        self._window = self._window.append(*messages)

    @property
    def window(self) -> tuple[ChatMessage, ...]:
        """本轮结束时的窗口. 驱动方拿它接着往下攒 (ADR-0041 决策 4).

        原先驱动方是自己重建一份跨轮历史 (用户原话 + 一行工具结论 + 助手回复), 工具结果
        整个不跨回合. 那是在工具结果还带着几 KB 正文的时候的取舍 —— 正文进跨轮历史等于
        把回合内溢出提前到第二轮.

        ADR-0041 决策 6 之后正文本来就不进窗口了, 一条结果只剩摘要与结构化字段, 于是原样
        留着比重述一遍便宜也准确. 更要紧的是: 窗口只追加, 而"跨轮重建一份不一样的历史"
        本身就是一次中段改写 —— 上一轮发出去的前缀, 下一轮对不上了.
        """
        return self._messages

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
        return self._caller.partial_answer

    # ---- 循环推进 ----

    def _advance(self) -> LoopStepResult:
        """调一次模型, 把产出翻译成动作."""
        self._check_workspace_changes(WorkspaceChangeSource.EXTERNAL)
        self._publish_workspace_notice()
        over_budget = self._check_model_budget()
        if over_budget is not None:
            return over_budget

        too_long = self._fit_window()
        if too_long is not None:
            return too_long

        # 顺带把 tool 清掉: 这一步是在想, 不在跑工具. 不清的话上一次调用的工具名会
        # 一直挂在后面每一行上, 读日志的人会以为那几行也属于它.
        update_run_context(step=self._events.next_step(), tool="")
        request = self._build_request()
        update_run_context(request_id=request.request_id)

        transport = self._transport_policy.resolve(request.origin)

        _log.info(
            "model.request",
            call_index=self._model_calls,
            origin=request.origin.value,
            messages=len(request.messages),
            tools=len(self._tools),
            transport=transport.value,
        )
        if _log.enabled_for_debug():
            # 先问再拼: _transcript_view 要走一遍整段 transcript, 而 DEBUG 关掉时
            # 那份结果会被原样丢掉 —— 参数是在调用之前求值的, 级别检查拦不住它.
            _log.debug(
                "model.request.messages", messages=transcript_view(self._messages)
            )
        self._events.model_started(
            request_id=request.request_id, call_index=self._model_calls
        )
        try:
            _log.info("llm.context", message=str(request.messages))
            outcome = self._caller.invoke(request, transport)
        except ModelCancelledError as exc:
            _log.warning("model.cancelled", message=str(exc))
            return self._stop(LoopStopReason.USER_CANCELLED, actionable_message(exc))
        except MalformedToolCallError as exc:
            # 绝不猜一个参数补上去, 那等于替模型编参数.
            #
            # 但"不替它补"和"不告诉它"是两件事. 只中止的话, 模型这一轮什么反馈都拿不到,
            # 下次还会原样再来一次; 告诉它坏在哪, 它自己能改. 这里只做后者.
            return self._retry_malformed(str(exc))
        except ModelContextOverflowError as exc:
            return self._recover_from_overflow(exc)
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
            # ModelCaller 已经自己收过尾 (中断 / 取消), 这里只补 turn 终态.
            self._turn_finished(outcome.reason)
            return outcome
        # 模型生成期间也可能有人改了工作区. 如果这次本来要直接回答, 不能把一个基于旧
        # 文件状态的答案当成最终答案; 追加事实后重新请求一次让模型重新判断.
        self._check_workspace_changes(WorkspaceChangeSource.EXTERNAL)
        if self._workspace_changes and not outcome.tool_calls:
            self._publish_workspace_notice()
            return self._advance()
        if outcome.empty:
            _log.warning("model.empty_response")
            return self._stop(
                LoopStopReason.MODEL_ERROR_BLOCKING,
                render_notice("stop.empty_response"),
            )

        # markup 检查必须在 _remember_assistant 之前: 这批调用一个都不会派发, 把带
        # tool_calls 的 assistant 消息写进 transcript 就欠下一堆永远等不到的
        # tool result, 下一次请求会因此残缺 (§10).
        for call in outcome.tool_calls:
            marker = protocol_markup_in(call)
            if marker is not None:
                _log.warning(
                    "model.protocol_markup",
                    tool=call.name,
                    marker=marker,
                    arguments=call.arguments,
                )
                return self._retry_malformed(
                    render_notice(
                        "loop.malformed_detail", tool=call.name, marker=marker
                    )
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
                return self._stop(
                    LoopStopReason.POLICY_DENIED, render_notice("stop.halt")
                )
            outcome = ModelOutcome(text=outcome.text)

        self._remember_assistant(outcome)
        if outcome.tool_calls:
            self._pending_calls = list(outcome.tool_calls)
            # 模型这一轮请求的调用已经全部确定。先把整批意图发给观察端，再派发第一个，
            # 页面才能在任何工具真正启动之前画出完整队列。裁决与执行事实仍由协调器发布，
            # 这里不会把“模型请求了”说成“必然会执行”。
            self._events.tool_batch(outcome.tool_calls)
            # 不发决策摘要: "模型请求 N 个工具调用"只是把界面上已经画着的东西再说一遍,
            # 而它会占掉展开层里最显眼的那一行, 把真正的摘要 (连续无新信息, 格式损坏
            # 重试) 挤成同一种东西。派发本身不是一次需要解释的决策。
            return self._dispatch_next(reason="")
        self._dispatched = None
        self._events.next_step()
        return self._decision("模型已产出最终回答", AnswerAction(text=outcome.text))

    def _dispatch_next(self, *, reason: str) -> LoopStepResult:
        """派发队列里的下一个工具调用. 一次一个, 不并行."""
        # 模型思考和真正执行之间也留出一个外部修改窗口. 这个检查点还没有 agent 工具
        # 在运行, 因此这里发现的变化明确归为 external.
        self._check_workspace_changes(WorkspaceChangeSource.EXTERNAL)
        call = self._pending_calls.pop(0)
        if self._progress.is_repeat(call):
            return self._reject_repeat(call)
        self._dispatched = call
        self._tool_calls += 1
        update_run_context(step=self._events.next_step(), tool=call.name)
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
        notice = self._progress.repeat_notice(render_call(call))
        self._append(
            ChatMessage(
                role=MessageRole.TOOL,
                content=(
                    ToolResultBlock(
                        tool_call_id=call.tool_call_id,
                        content=notice,
                        is_error=True,
                    ),
                ),
            ),
        )
        self._events.tool_rejected(call, notice=notice, code="repeated_call")
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
        # 上一个检查点位于工具派发前. 这段差异属于刚刚完成的 agent 工具操作, 包括 shell
        # 这类无法在结果中列出具体 mutation path 的工具.
        self._check_workspace_changes(WorkspaceChangeSource.AGENT)
        _log.info(
            "tool.observed",
            tool=call.name,
            is_error=observation.is_error,
            disposition=observation.disposition.value,
            chars=len(observation.content),
        )
        # 回填正文单独走 debug: info 级别无条件打整段, 一次带正文的读取就是几 KB,
        # 而日志文件是每一轮都在写的. 协调器那边早就是这个写法.
        if _log.enabled_for_debug():
            _log.debug("tool.observed.content", content=observation.content)
        content = labelled(call, observation.content)
        self._append(
            ChatMessage(
                role=MessageRole.TOOL,
                content=(
                    ToolResultBlock(
                        tool_call_id=call.tool_call_id,
                        content=content,
                        is_error=observation.is_error,
                        provenance=observation.provenance,
                    ),
                ),
            ),
        )
        # 这里不发 TOOL_COMPLETED: 执行结果的权威事实在协调器那边 (状态, 耗时, 退出码,
        # 是否产生了副作用). 循环只拿到一段回填文本, 用它冒充执行结论会让终端显示的
        # "完成"与真正发生的事脱节.
        self._progress.track(observation)
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
            self._abandon_pending(render_notice("loop.review_abandon"))
            self._pending_calls = []
            return self._stop(LoopStopReason.WAIT_PLAN_REVIEW, None)
        halt = self._progress.should_close_tools(observation)
        if halt is not None:
            return self._close_tools(halt)
        if self._pending_calls:
            return self._dispatch_next(reason="继续派发同一批中的下一个工具调用")
        # 提醒只在整批工具结果都回填完之后追加. 插在两条 tool result 中间会打断
        # "assistant 的 tool_calls -> 配对的 tool result"这段连续区, 供应商会拒.
        nudge = self._progress.barren_nudge()
        if nudge is not None:
            notice, summary = nudge
            self._append(
                ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),)),
            )
            self._events.decision(summary)
        return self._advance()

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
        self._append(
            ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),)),
        )
        self._events.decision(notice)
        return self._advance()

    def _recover_from_overflow(self, exc: ModelGatewayError) -> LoopStepResult:
        """供应商说上下文太长: 强压一次再发, 而不是让这一轮作废.

        走到这里说明估算与供应商的分词对不上 —— ``_fit_window`` 算着放得下, 实际放不下.
        近似估算永远会有这种偏差, 所以这条路必须存在.

        降级路径是**强制一次淘汰**: `fit(force=True)` 跳过水位判断, 无条件淘汰一次.
        不另开一个 summarize_now 入口 —— 两条路各自决定切点与保留策略, 迟早有一条漏掉
        "用户原话逐字保留"这类规则.

        只给一次: 淘汰跑完之后窗口只剩一段交接说明加最近几条, 再超就不是淘汰能解决的
        问题了. 那时按 CONTEXT_COMPACTION_REQUIRED 停 —— 那是可恢复的暂停, 而
        MODEL_ERROR_BLOCKING 会把已经跑了几十步的一轮直接判死.
        """
        if (
            self._context is None
            or self._context_budget is None
            or self._overflow_compactions
        ):
            _log.error("window.overflow_unrecoverable", message=str(exc))
            return self._stop(
                LoopStopReason.CONTEXT_COMPACTION_REQUIRED,
                render_notice("context.compaction_failed"),
            )
        self._overflow_compactions += 1
        result = self._context.fit(
            self._window,
            budget=self._context_budget,
            # 供应商说放不下, 那就是放不下: 估算说什么都不作数了.
            force=True,
            session_id=self._session_id,
            turn_id=self._turn_id or "",
            system_prompt=(
                "" if self._assembled is None else self._assembled.system_prompt
            ),
            tools=self._tools,
        )
        self._window = result.window
        self._compaction_drafts.extend(result.drafts)
        self._usage_drafts.extend(result.usage_drafts)
        self._events.compaction(result)
        if not result.drafts:
            _log.error("window.overflow_uncompactable", message=str(exc))
            return self._stop(
                LoopStopReason.CONTEXT_COMPACTION_REQUIRED,
                render_notice("context.compaction_failed"),
            )
        _log.warning(
            "window.overflow_evicted",
            message=str(exc),
            messages=len(self._messages),
            estimated_input=result.estimated_input,
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
                render_notice("stop.malformed", count=self._malformed_responses),
            )
        notice = render_notice("loop.malformed", detail=detail)
        self._append(
            ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),)),
        )
        self._events.decision(f"工具调用格式损坏, 重试: {detail}")
        return self._advance()

    def _abandon_pending(self, notice: str) -> None:
        """给还在排队的调用补上 tool result.

        协议要求 assistant 消息里每个 tool_call 都有对应的 tool result: 直接丢掉它们,
        下一次请求就是残缺的, 供应商会拒。所以是"标记为未执行", 不是"当作没发生过".
        """
        for call in self._pending_calls:
            detail = render_notice("loop.abandoned_call", notice=notice)
            self._append(
                ChatMessage(
                    role=MessageRole.TOOL,
                    content=(
                        ToolResultBlock(
                            tool_call_id=call.tool_call_id,
                            content=detail,
                            is_error=True,
                        ),
                    ),
                ),
            )
            self._events.tool_abandoned(call, notice=detail)

    # ---- 预算 ----

    def _check_workspace_changes(self, source: WorkspaceChangeSource) -> None:
        if self._workspace_monitor is None:
            return
        self._workspace_changes.extend(self._workspace_monitor.checkpoint(source))

    def _publish_workspace_notice(self) -> None:
        if not self._workspace_changes:
            return
        changes = tuple(self._workspace_changes)
        self._workspace_changes.clear()
        notice = render_notice("loop.workspace_changed", changes=changes)
        self._append(ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),)))
        self._events.workspace_changed(changes)
        _log.info(
            "workspace.changed",
            changes=[
                {
                    "path": change.path,
                    "kind": change.kind.value,
                    "source": change.source.value,
                }
                for change in changes
            ],
        )

    def _check_model_budget(self) -> LoopStop | None:
        limit = _DEFAULT_MAX_MODEL_CALLS
        if self._model_calls >= limit:
            _log.warning("loop.model_budget_exhausted", limit=limit)
            return self._stop(
                LoopStopReason.BUDGET_EXHAUSTED,
                render_notice("stop.model_budget", limit=limit),
            )
        return None

    def _fit_window(self) -> LoopStop | None:
        """调模型之前看一眼窗口撞没撞高水位 (ADR-0041 决策 5).

        放在这里而不是等网关抛 ModelContextOverflowError: 那条路上请求已经组好了, 而且
        它今天是终止性的 —— 一轮跑了二十步的工作会因为最后一次调用超窗而整个作废.

        淘汰完仍然放不下时停在 CONTEXT_COMPACTION_REQUIRED, 不把一个必然被拒的请求
        发出去. 它归类为 RESUMABLE_PAUSE: 这一轮干不下去了, 但会话没坏.
        """
        if self._context is None:
            return None
        result = self._context.fit(
            self._window,
            budget=self._context_budget,
            session_id=self._session_id,
            turn_id=self._turn_id or "",
            system_prompt=(
                "" if self._assembled is None else self._assembled.system_prompt
            ),
            tools=self._tools,
        )
        self._window = result.window
        self._compaction_drafts.extend(result.drafts)
        # 压缩那次模型调用的账 (ADR-0037): 与循环自己的调用走同一条路 —— 攒进
        # _usage_drafts 交给 AgentTurnService 落盘, 同时发一条 MODEL_USAGE 让终端
        # 与页面把它算进本轮合计.
        self._usage_drafts.extend(result.usage_drafts)
        self._events.compaction(result)
        if result.drafts:
            _log.info(
                "window.fit",
                drafts=len(result.drafts),
                tokens_saved=sum(draft.tokens_saved for draft in result.drafts),
                messages=len(result.window.messages),
                over_allowance=result.over_allowance,
            )
        if result.over_allowance:
            _log.error("window.over_allowance", messages=len(result.window.messages))
            return self._stop(
                LoopStopReason.CONTEXT_COMPACTION_REQUIRED,
                render_notice("context.compaction_failed"),
            )
        return None

    # ---- transcript ----

    def _remember_assistant(self, outcome: ModelOutcome) -> None:
        """把模型这一步写进 transcript.

        带 tool_calls 的 assistant 消息即使没有文本也必须留下: 后面的 tool result 要靠
        tool_call_id 关联到它 (§10), 丢了这条消息, 供应商侧就对不上号.
        """
        content = (TextBlock(outcome.text),) if outcome.text else ()
        self._append(
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=content,
                tool_calls=outcome.tool_calls,
            ),
        )

    # ---- 组请求与收尾 ----

    def _build_request(self) -> ModelRequest:
        self._model_calls += 1
        return ModelRequest(
            request_id=self._new_request_id(),
            session_id=self._session_id,
            turn_id=self._turn_id or "",
            origin=(
                # 带工具目录的调用属于 act 阶段; 纯对话仍记 chat (ADR-0011 §3.3).
                RequestOrigin.TOOL_CALL if self._tools else RequestOrigin.CHAT
            ),
            # [5] 窗口 + [6] 状态帧. 顺序在 AssembledContext 里定, 这里不自己拼.
            messages=(
                self._messages
                if self._assembled is None
                else self._assembled.to_request_messages(self._messages)
            ),
            params=ModelParams(),
            system_prompt=(
                self._assembled.system_prompt if self._assembled is not None else None
            ),
            tools=self._tools,
            # 没有 cache_hint 这类字段: 当前唯一的 adapter 走自动前缀缓存, 标不标
            # 一样. 原先那个字段已按此删除, 理由见 llm/gateway/cache.py 的模块注释.
            cancel_token=self._new_cancel_token(),
        )

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
            steps=self._events.step,
            elapsed_ms=self._events.elapsed_ms(),
        )
        self._finished = True
        self._turn_finished(reason, detail=message or "")
        return LoopStop(reason, message=message)

    def _turn_finished(self, reason: LoopStopReason, *, detail: str = "") -> None:
        self._events.turn_finished(
            reason,
            detail=detail,
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
        )

    def _decision(self, reason_summary: str, action: LoopAction) -> LoopAction:
        """产出一个动作, 同时把它的理由摘要发出去 (空摘要不发事件, 见发布器)."""
        self._events.decision(reason_summary)
        return action


def _schemas_of(catalog: ToolCatalog | None) -> tuple[ToolSchema, ...]:
    """把目录翻译成模型侧 schema. 目录为空表示本档不暴露任何工具."""
    return () if catalog is None else catalog.to_model_schemas()
