"""BuiltinAgentLoop: MVP 唯一的 ReAct 循环实现 (ADR-0010 §3 / §5 / §13-3, ADR-0049).

一轮的形状:

    模型调用 -> 有 tool_calls?
      ├─ 是: 逐个产出 ToolRequestAction, 每拿回一条 observation 就写进 transcript,
      │      全部回填完再调一次模型 (这就是 ReAct 的 act -> observe -> reason 环)
      └─ 否: AnswerAction -> observe 后 LoopStop(FINAL_ANSWER)

三条与安全直接相关的约束:

- **循环只产出意图, 不执行工具.** ToolRequestAction 交给 AgentTurnService, 由它经
  ToolRequestCoordinator 走完整条安全管线. 循环拿不到 ToolRegistry, 也拿不到
  ToolRuntime —— 想绕过协调器在这里做不到.
- **一次只派发一个工具调用.** 模型一口气要了三个工具时, 后两个排队等前一个的
  observation. 并行需要知道冲突域 (ADR-0004 §10), 而那是安全层的知识, 循环没有.
- **工具目录来自 LoopInput.** 循环不自己算"哪些工具可见", 那是 mode 能力门的事;
  它只负责把目录翻译成模型能看懂的 schema.

**这个模块只剩控制流** (ADR-0049 决策 4). "下一步做什么"的判断全在规则里
(``rules/``), 每条规则在它关心的时机上给一个处置 (``verdicts``), 循环负责执行处置:
换窗口, 追加消息, 拒绝一个调用, 收工具目录, 停. 规则表怎么跑见 ``rule_table``.

留在循环里不搬的: 派一个工具等结果并回填成配对的工具结果, 给排队中的调用补结果,
写 assistant 消息, 组请求, 窗口只追加, 一次只派一个. 这些是协议本身, 不是判断.

运行事件按 ADR-0016 发布到 AgentRunEventBus. 分工: 循环拥有 turn 生命周期, 步骤切换,
模型调用和"模型请求了哪个工具" (TOOL_QUEUED); 工具真正被裁决与执行之后的事件由
ToolRequestCoordinator 发布 —— 循环看不见裁决与执行, 编不出那些事实. 规则只交回一句
摘要, 事件由循环在执行处置时发; 规则自己看见的事实 (压缩发生了) 经账本报出去.

读这个文件只需要跟着 start -> _advance -> _dispatch_next -> _observe_tool -> _stop
这条线走.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence

from forgecli.application.agent_loop.ledger import TurnLedger
from forgecli.application.agent_loop.model_invoker import (
    AgentModelInvoker,
    ModelOutcome,
)
from forgecli.application.agent_loop.rule import LoopRule, LoopView
from forgecli.application.agent_loop.rule_table import RuleTable
from forgecli.application.agent_loop.run_events import LoopEventPublisher
from forgecli.application.agent_loop.transcript import labelled, transcript_view
from forgecli.application.agent_loop.verdicts import (
    CloseTools,
    Deny,
    Inject,
    Reask,
    Replace,
    Rewrite,
)
from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.llm.error_hints import actionable_message
from forgecli.application.llm.gateway.errors import (
    ModelCancelledError,
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
    ToolRequest,
    ToolRequestAction,
)
from forgecli.domain.agent.phase import LoopPhase
from forgecli.domain.agent.state import AssembledContext, LoopInput
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.context.compaction import CompactionDraft
from forgecli.domain.context.window import Window
from forgecli.domain.conversation.message import (
    ChatMessage,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.intents import SessionMode
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


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


class BuiltinAgentLoop:
    """受控 ReAct 循环: 每 turn 一个新实例 (由装配方以工厂创建)."""

    def __init__(
        self,
        gateway: LlmGateway,
        usage_meter: UsageMeter,
        *,
        rules: Sequence[LoopRule],
        model_transport_policy: ModelTransportPolicy,
        request_id_factory: Callable[[], str] = _new_request_id,
        cancel_token_factory: Callable[[], CancelToken | None] = lambda: None,
        event_bus: AgentRunEventBus | None = None,
        timer: Callable[[], float] = time.monotonic,
        workspace_snapshot_provider: WorkspaceSnapshotProvider | None = None,
    ) -> None:
        self._new_request_id = request_id_factory
        self._new_cancel_token = cancel_token_factory
        self._timer = timer
        self._transport_policy = model_transport_policy
        self._events = LoopEventPublisher(event_bus, timer=timer)
        # 本轮的账本: 用量草稿与压缩记录. 压缩产生的草稿也进同一份 (ADR-0037).
        self._ledger = TurnLedger(self._events)
        self._invoker = AgentModelInvoker(
            gateway,
            usage_meter,
            self._events,
            on_usage=self._ledger.add_usage,
            timer=timer,
        )
        self._rules = RuleTable(rules, events=self._events)
        self._phase = LoopPhase.NOT_STARTED
        self._turn_id: str | None = None
        self._session_id = ""
        self._mode: SessionMode | None = None
        # 本轮的会话窗口: 每次模型调用都基于它, 工具结果按 §10 写回这里.
        #
        # 存 Window 而不是裸元组, 是因为 `evicted_count` 必须跨调用累计 —— 淘汰要靠它
        # 判断"头一条是不是上一份交接说明".
        self._window = Window()
        # start() 之前为 None. 循环不自己造提示词 —— 编译是 application 的事.
        self._assembled: AssembledContext | None = None
        self._tools: tuple[ToolSchema, ...] = ()
        self._tools_closed = False
        # 第 4 步搬进规则. 暂留.
        self._workspace_monitor = (
            None
            if workspace_snapshot_provider is None
            else WorkspaceChangeMonitor(workspace_snapshot_provider)
        )
        self._workspace_changes: list[WorkspaceChange] = []
        # 模型一次要了多个工具时的等待队列, 以及正在等 observation 的那一个.
        self._pending_calls: list[ToolCall] = []
        self._dispatched: ToolCall | None = None
        # 规则在一批工具还没排空时要求追加的消息. 攒着, 整批回填完再写: 插在两条
        # 工具结果中间会打断配对的连续区, 供应商会拒.
        self._deferred: list[Inject] = []
        self._model_calls = 0
        self._tool_calls = 0

    # ---- AgentLoop 端口 ----

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        if self._phase is not LoopPhase.NOT_STARTED:
            raise RuntimeError("BuiltinAgentLoop 每轮只能 start 一次 (每 turn 新实例)")
        self._turn_id = loop_input.turn_id
        self._session_id = loop_input.session_id
        self._mode = loop_input.mode
        self._window = Window(messages=loop_input.context.initial_window)
        # 本轮所有模型调用复用同一份上下文前缀. 不在工具调用之间隐式换 (ADR-0018 §6.2):
        # 换了之后"模型为什么突然改了行为"就再也对不上任何一条事件.
        self._assembled = loop_input.context
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
        budget = loop_input.context.budget
        _log.info(
            "loop.start",
            mode=loop_input.mode.value,
            tools=[schema.name for schema in self._tools],
            messages=len(self._messages),
            prompt_fingerprint=loop_input.context.policy.fingerprint,
            rules=list(self._rules.names),
            context_window=None if budget is None else budget.context_window,
            context_allowance=None if budget is None else budget.allowance,
        )
        return self._advance()

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        match self._phase:
            case LoopPhase.NOT_STARTED:
                raise RuntimeError("BuiltinAgentLoop 未 start, 不接受 observe")
            case LoopPhase.FINISHED:
                raise RuntimeError("BuiltinAgentLoop 已结束, 不接受 observe")
            case LoopPhase.AWAITING_ANSWER_ACK:
                # 上一步产出的是 AnswerAction: 驱动方确认送达后本轮就结束.
                return self._stop(LoopStop(LoopStopReason.FINAL_ANSWER))
            case LoopPhase.AWAITING_TOOL_RESULT:
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

        ADR-0041 决策 6 之后正文本来就不进窗口, 一条结果只剩摘要与结构化字段, 于是原样
        留着比重述一遍便宜也准确. 更要紧的是: 窗口只追加, 而"跨轮重建一份不一样的历史"
        本身就是一次中段改写 —— 上一轮发出去的前缀, 下一轮对不上了.
        """
        return self._messages

    @property
    def ledger(self) -> TurnLedger:
        """本轮的账本 (落盘由 AgentTurnService 执行)."""
        return self._ledger

    @property
    def usage_drafts(self) -> tuple[UsageRecordDraft, ...]:
        return self._ledger.usage_drafts

    @property
    def compaction_drafts(self) -> tuple[CompactionDraft, ...]:
        return self._ledger.compaction_drafts

    @property
    def partial_answer(self) -> str | None:
        """流式中断时已累积的部分回答文本 (无则为 None)."""
        return self._invoker.partial_answer

    # ---- 循环推进 ----

    def _view(self) -> LoopView:
        """规则看到的只读快照. 每次现造, 便宜."""
        return LoopView(
            session_id=self._session_id,
            turn_id=self._turn_id or "",
            mode=self._mode if self._mode is not None else SessionMode.PLAN,
            phase=self._phase,
            window=self._window,
            tools=self._tools,
            tools_closed=self._tools_closed,
            budget=None if self._assembled is None else self._assembled.budget,
            system_prompt=(
                "" if self._assembled is None else self._assembled.system_prompt
            ),
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
            pending_calls=len(self._pending_calls),
            ledger=self._ledger,
        )

    def _advance(self) -> LoopStepResult:
        """调一次模型, 把产出翻译成动作.

        四条"追加点东西再来一次"的路 (格式重试, 溢出强压, 工作区变更, 收工具) 都是
        `continue`, 不递归.
        """
        while True:
            # 工作区检查 (第 4 步搬进规则).
            self._check_workspace_changes(WorkspaceChangeSource.EXTERNAL)
            self._publish_workspace_notice()

            stop = self._rules.before_model(self._view, self._apply_rewrite)
            if isinstance(stop, LoopStop):
                return self._stop(stop)

            outcome = self._call_model()
            if isinstance(outcome, LoopStop):
                return self._stop(outcome)
            if isinstance(outcome, Reask | Rewrite):
                self._apply_retry(outcome)
                continue

            # 模型生成期间也可能有人改了工作区. 如果这次本来要直接回答, 不能把一个
            # 基于旧文件状态的答案当成最终答案 (第 4 步搬进规则).
            self._check_workspace_changes(WorkspaceChangeSource.EXTERNAL)
            if self._workspace_changes and not outcome.tool_calls:
                self._publish_workspace_notice()
                continue

            after = self._rules.after_model(outcome, self._view())
            if isinstance(after, Reask):
                self._apply_retry(after)
                continue
            if isinstance(after, LoopStop):
                return self._stop(after)
            if isinstance(after, Replace):
                outcome = after.outcome

            self._remember_assistant(outcome)
            return self._dispatch_or_answer(outcome)

    def _call_model(self) -> ModelOutcome | LoopStop | Reask | Rewrite:
        """组请求, 发出去, 把出错的路交给规则."""
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
            # 先问再拼: transcript_view 要走一遍整段 transcript, 而 DEBUG 关掉时
            # 那份结果会被原样丢掉 —— 参数是在调用之前求值的, 级别检查拦不住它.
            _log.debug(
                "model.request.messages", messages=transcript_view(self._messages)
            )
        self._events.model_started(
            request_id=request.request_id, call_index=self._model_calls
        )
        try:
            return self._invoker.invoke(request, transport)
        except ModelCancelledError as exc:
            # 取消不是规则能商量的事.
            _log.warning("model.cancelled", message=str(exc))
            return LoopStop(LoopStopReason.USER_CANCELLED, actionable_message(exc))
        except ModelGatewayError as exc:
            verdict = self._rules.on_model_error(exc, self._view())
            if isinstance(verdict, Reask | Rewrite | LoopStop):
                return verdict
            return self._unclaimed_model_error(exc)

    @staticmethod
    def _unclaimed_model_error(exc: ModelGatewayError) -> LoopStop:
        """没有一条规则认领的错: 一律停."""
        if isinstance(exc, ModelResponseParseError):
            # provider 的响应压根不是 JSON 这类传输层故障: 那不是模型的错, 追加纠错
            # 消息是在冤枉它, 重发同一条请求也不会好转.
            _log.exception("model.parse_failed", message=str(exc))
            return LoopStop(LoopStopReason.MODEL_ERROR_BLOCKING, str(exc))
        _log.exception(
            "model.gateway_failed", error=type(exc).__name__, message=str(exc)
        )
        return LoopStop(LoopStopReason.MODEL_ERROR_BLOCKING, actionable_message(exc))

    def _apply_rewrite(self, verdict: Rewrite) -> None:
        self._window = verdict.window
        self._events.decision(verdict.summary)

    def _apply_retry(self, verdict: Reask | Rewrite) -> None:
        if isinstance(verdict, Reask):
            self._append(*verdict.messages)
        else:
            self._window = verdict.window
        self._events.decision(verdict.summary)

    def _dispatch_or_answer(self, outcome: ModelOutcome) -> LoopStepResult:
        if outcome.tool_calls:
            self._pending_calls = list(outcome.tool_calls)
            # 模型这一轮请求的调用已经全部确定. 先把整批意图发给观察端, 再派发第一个,
            # 页面才能在任何工具真正启动之前画出完整队列. 裁决与执行事实仍由协调器发布,
            # 这里不会把"模型请求了"说成"必然会执行".
            self._events.tool_batch(outcome.tool_calls)
            # 不发决策摘要: "模型请求 N 个工具调用"只是把界面上已经画着的东西再说一遍.
            return self._dispatch_next(reason="")
        self._dispatched = None
        self._phase = LoopPhase.AWAITING_ANSWER_ACK
        self._events.next_step()
        return self._decision("模型已产出最终回答", AnswerAction(text=outcome.text))

    def _dispatch_next(self, *, reason: str) -> LoopStepResult:
        """派发队列里的下一个工具调用. 一次一个, 不并行."""
        # 模型思考和真正执行之间也留出一个外部修改窗口. 这个检查点还没有 agent 工具
        # 在运行, 因此这里发现的变化明确归为 external (第 4 步搬进规则).
        self._check_workspace_changes(WorkspaceChangeSource.EXTERNAL)
        call = self._pending_calls.pop(0)
        verdict = self._rules.before_dispatch(call, self._view())
        if isinstance(verdict, Deny):
            return self._deny(call, verdict)
        if isinstance(verdict, LoopStop):
            return self._stop(verdict)
        self._dispatched = call
        self._phase = LoopPhase.AWAITING_TOOL_RESULT
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

    def _deny(self, call: ToolCall, verdict: Deny) -> LoopStepResult:
        """规则挡下了这一个调用: 不执行, 把规则给的那段文字当它的工具结果回填.

        回填而不是静默跳过, 也不是直接停止本轮: 模型必须知道这个调用怎么了, 否则它
        只会原样再要一次. 这条消息进 transcript, 下一次模型调用就看得到.
        """
        self._append(
            ChatMessage(
                role=MessageRole.TOOL,
                content=(
                    ToolResultBlock(
                        tool_call_id=call.tool_call_id,
                        content=verdict.notice,
                        is_error=True,
                    ),
                ),
            ),
        )
        self._events.tool_rejected(call, notice=verdict.notice, code=verdict.code)
        self._dispatched = None
        if self._pending_calls:
            return self._dispatch_next(reason="跳过被拒的调用, 派发下一个")
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
        # 这类无法在结果中列出具体 mutation path 的工具 (第 4 步搬进规则).
        self._check_workspace_changes(WorkspaceChangeSource.AGENT)
        _log.info(
            "tool.observed",
            tool=call.name,
            is_error=observation.is_error,
            disposition=observation.disposition.value,
            chars=len(observation.content),
        )
        # 回填正文单独走 debug: info 级别无条件打整段, 一次带正文的读取就是几 KB.
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
        # 是否改了文件). 循环只拿到一段回填文本, 用它冒充执行结论会让终端显示的
        # "完成"与真正发生的事脱节.
        verdict = self._rules.after_observe(call, observation, self._view())
        if isinstance(verdict, LoopStop):
            return self._stop(verdict)
        if isinstance(verdict, CloseTools):
            return self._close_tools(verdict.notice)
        if isinstance(verdict, Inject):
            self._deferred.append(verdict)
        if self._pending_calls:
            return self._dispatch_next(reason="继续派发同一批中的下一个工具调用")
        # 整批工具结果都回填完了, 攒着的消息现在才能写.
        for inject in self._deferred:
            self._append(*inject.messages)
            self._events.decision(inject.summary)
        self._deferred = []
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
        self._deferred = []
        self._tools = ()
        self._tools_closed = True
        self._dispatched = None
        self._append(
            ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),)),
        )
        self._events.decision(notice)
        return self._advance()

    def _abandon_pending(self, notice: str) -> None:
        """给还在排队的调用补上 tool result, 并清空队列.

        协议要求 assistant 消息里每个 tool_call 都有对应的 tool result: 直接丢掉它们,
        下一次请求就是残缺的, 供应商会拒. 所以是"标记为未执行", 不是"当作没发生过".
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
        self._pending_calls = []

    # ---- 工作区 (第 4 步搬进规则) ----

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
            cancel_token=self._new_cancel_token(),
        )

    def _stop(self, stop: LoopStop) -> LoopStop:
        """本轮的唯一出口. 排队中的调用在这里补上"未执行"的配对结果."""
        if self._pending_calls:
            # 计划评审是今天唯一带着队列停下的路; 其余停止发生在队列排空之后.
            notice = (
                render_notice("loop.review_abandon")
                if stop.reason is LoopStopReason.WAIT_PLAN_REVIEW
                else render_notice("loop.abandoned_call", notice=stop.reason.value)
            )
            self._abandon_pending(notice)
        # 停下来之后的行不该还挂着本轮最后一步的标识. session / turn 由外层
        # AgentTurnService 的 bind 负责还原, 这里只清自己写进去的那几个.
        update_run_context(step=0, request_id="", tool="")
        _log.info(
            "loop.stop",
            reason=stop.reason.value,
            message=stop.message,
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
            steps=self._events.step,
            elapsed_ms=self._events.elapsed_ms(),
        )
        self._phase = LoopPhase.FINISHED
        self._events.turn_finished(
            stop.reason,
            detail=stop.message or "",
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
        )
        return stop

    def _decision(self, reason_summary: str, action: LoopAction) -> LoopAction:
        """产出一个动作, 同时把它的理由摘要发出去 (空摘要不发事件, 见发布器)."""
        self._events.decision(reason_summary)
        return action


def _schemas_of(catalog: ToolCatalog | None) -> tuple[ToolSchema, ...]:
    """把目录翻译成模型侧 schema. 目录为空表示本档不暴露任何工具."""
    return () if catalog is None else catalog.to_model_schemas()
