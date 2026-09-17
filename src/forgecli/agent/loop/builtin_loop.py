"""BuiltinAgentLoop: 一次查询的完整生命周期 (ADR-0010 §3 / §5, ADR-0049, ADR-0051).

一轮的形状:

    while True:
        ctx = assemble(window)           # 每次调模型前现组: 提示词, 工具, 通知, 预算
        追加 ctx.notices -> 规则 (压缩了就再组一次) -> 调模型 -> 规则 -> 写 assistant
        没有 tool_calls -> 停 (FINAL_ANSWER, 带回答)
        for call in 这批:
            规则 -> execute(call) -> 回填工具结果 -> 规则 (停 / 收工具 / 攒提醒)
        写攒着的提醒 -> 回到顶上再想

三条与安全直接相关的约束:

- **循环只会调外层交给它的两个函数.** `execute` 是 AgentTurnService 经
  CoordinatorToolDispatcher 走完整条安全管线的方法, `assemble` 是它读计划, 记忆, 目录,
  FORGE.md 组一份材料的方法. 循环拿不到 dispatcher, 拿不到 ToolRegistry, 也拿不到
  ToolRuntime 与各个服务 —— 单一入口由 dispatcher 的类型守着 (ADR-0051).
- **整批交给执行链.** 模型一口气要了三个工具时, 三个一起交出去; 按只读性分批并发
  是执行链的事 (ADR-0054 决策 4), 循环只看到一批进一批出.
- **工具目录来自 assemble().** 循环不自己算"哪些工具可见", 那是 mode 能力门的事.
  目录一旦被规则收掉, 后面每次 assemble 带回来的目录都不再发给模型.

**这个模块只剩控制流** (ADR-0049 决策 4). "下一步做什么"的判断全在规则里
(``rules/``): 每个时机一列方法, 每个方法给一个处置 (``verdicts``), 循环负责执行处置:
换窗口, 追加消息, 拒绝一个调用, 收工具目录, 停. 哪个时机跑什么见 ``rules/__init__``,
怎么跑见 ``rule_table``.

留在循环里不搬的: 执行一个等一个并回填成配对的工具结果, 给排队中的调用补结果,
写 assistant 消息, 组请求, 窗口只追加. 这些是协议本身, 不是判断.

运行事件按 ADR-0016 发布到 AgentRunEventBus. 分工: 循环拥有 turn 生命周期, 步骤切换,
模型调用和"模型请求了哪个工具" (TOOL_QUEUED); 工具真正被裁决与执行之后的事件由
执行链 (tools/execution.py) 发布 —— 循环看不见裁决与执行, 编不出那些事实. 规则只交回一句
摘要, 事件由循环在执行处置时发; 规则自己看见的事实 (压缩发生了) 经账本报出去.

读这个文件只需要跟着 run -> _think_and_act -> _run_batch -> _execute -> _stop 这条线走.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from forgecli.agent.loop.event_publisher import LoopEventPublisher
from forgecli.agent.loop.ledger import TurnLedger
from forgecli.agent.loop.model_invoker import AgentModelInvoker, ModelOutcome
from forgecli.agent.loop.rule import LoopView
from forgecli.agent.loop.rule_table import RuleRunner, RuleTable
from forgecli.agent.loop.transcript import labelled, transcript_view
from forgecli.agent.loop.verdicts import (
    CloseTools,
    Deny,
    Inject,
    Reask,
    Replace,
    Rewrite,
)
from forgecli.agent.run_bus import AgentRunEventBus
from forgecli.common.cancellation import CancelToken
from forgecli.common.observability.context import update as update_run_context
from forgecli.common.observability.log import get_log
from forgecli.conversation.notices import guidance
from forgecli.core.agent.actions import (
    ContextAssembly,
    LoopObservation,
    LoopResult,
    LoopStop,
    ToolExecutor,
)
from forgecli.core.agent.phase import LoopPhase
from forgecli.core.agent.state import AssembledContext, LoopInput
from forgecli.core.agent.stop import LoopStopReason
from forgecli.core.context.window import Window
from forgecli.core.message import ChatMessage, TextBlock, ToolResultBlock
from forgecli.core.model.origin import RequestOrigin
from forgecli.core.model.params import ModelParams
from forgecli.core.model.request import ModelRequest
from forgecli.core.permissions import PermissionMode
from forgecli.core.tool.tool_call import ToolCall, ToolSchema
from forgecli.core.turn import MessageRole
from forgecli.llm.error_hints import actionable_message
from forgecli.llm.gateway.errors import (
    ModelCancelledError,
    ModelGatewayError,
    ModelResponseParseError,
)
from forgecli.llm.gateway.gateway import LlmGateway
from forgecli.llm.metering import UsageMeter
from forgecli.llm.transport_policy import ModelTransportPolicy
from forgecli.prompt import prompts

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
        rules: RuleTable,
        model_transport_policy: ModelTransportPolicy,
        request_id_factory: Callable[[], str] = _new_request_id,
        cancel_token_factory: Callable[[], CancelToken | None] = lambda: None,
        event_bus: AgentRunEventBus | None = None,
        timer: Callable[[], float] = time.monotonic,
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
        self._rules = RuleRunner(rules, events=self._events)
        self._phase = LoopPhase.NOT_STARTED
        self._turn_id: str | None = None
        self._session_id = ""
        self._mode: PermissionMode | None = None
        # 本轮的会话窗口: 每次模型调用都基于它, 工具结果按 §10 写回这里.
        #
        # 存 Window 而不是裸元组, 是因为 `evicted_count` 必须跨调用累计 —— 淘汰要靠它
        # 判断"头一条是不是上一份交接说明".
        self._window = Window()
        # 这次调用的材料. 每次调模型前由 assemble() 现组; 第一次之前为 None.
        self._context: AssembledContext | None = None
        # 目录一旦被规则收掉, 后面每次 assemble 带回来的目录都不再发给模型.
        self._tools_closed = False
        # 模型一次要了多个工具时的等待队列.
        self._pending_calls: list[ToolCall] = []
        # 模型最后那次回答的正文; 没走到回答就停的话为 None.
        self._answer: str | None = None
        # 规则在一批工具还没排空时要求追加的消息. 攒着, 整批回填完再写: 插在两条
        # 工具结果中间会打断配对的连续区, 供应商会拒.
        self._deferred: list[Inject] = []
        self._model_calls = 0
        self._tool_calls = 0

    # ---- 入口 ----

    def run(
        self,
        loop_input: LoopInput,
        *,
        execute: ToolExecutor,
        assemble: ContextAssembly,
    ) -> LoopResult:
        """跑完一次查询: 想, 做, 记, 直到模型不再要工具.

        `execute` 与 `assemble` 是外层交进来的两个函数, 循环只会调它们 (ADR-0051).
        """
        if self._phase is not LoopPhase.NOT_STARTED:
            raise RuntimeError("BuiltinAgentLoop 每轮只能 run 一次 (每 turn 新实例)")
        self._phase = LoopPhase.RUNNING
        self._turn_id = loop_input.turn_id
        self._session_id = loop_input.session_id
        self._mode = loop_input.mode
        self._window = Window(
            messages=loop_input.initial_window,
            evicted_count=loop_input.evicted_count,
        )
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
            messages=len(self._messages),
            rules=self._rules.table.describe(),
        )
        stop = self._think_and_act(execute, assemble)
        return LoopResult(
            stop=stop,
            answer=self._answer,
            partial_answer=self._invoker.partial_answer,
            window=self._messages,
            evicted_count=self._window.evicted_count,
            usage_drafts=self._ledger.usage_drafts,
            compaction_drafts=self._ledger.compaction_drafts,
        )

    # ---- 窗口与快照 ----

    @property
    def _messages(self) -> tuple[ChatMessage, ...]:
        """窗口里的消息. 读多写少, 所以只读走这里, 写走 `_append` 与 `_window`."""
        return self._window.messages

    def _append(self, *messages: ChatMessage) -> None:
        """窗口只追加 (ADR-0041 决策 4). 全部写入都走这一个口子."""
        self._window = self._window.append(*messages)

    # ---- 循环推进 ----

    def _view(self) -> LoopView:
        """规则看到的只读快照. 每次现造, 便宜."""
        return LoopView(
            session_id=self._session_id,
            turn_id=self._turn_id or "",
            mode=self._mode if self._mode is not None else PermissionMode.PLAN,
            phase=self._phase,
            window=self._window,
            tools=self._tools,
            tools_closed=self._tools_closed,
            budget=None if self._context is None else self._context.budget,
            system_prompt=(
                "" if self._context is None else self._context.system_prompt
            ),
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
            pending_calls=len(self._pending_calls),
            ledger=self._ledger,
        )

    @property
    def _tools(self) -> tuple[ToolSchema, ...]:
        """这次发给模型的工具. 目录收掉之后一直是空, 不管 assemble 带回来什么."""
        if self._tools_closed or self._context is None:
            return ()
        return self._context.tools

    def _think_and_act(
        self, execute: ToolExecutor, assemble: ContextAssembly
    ) -> LoopStop:
        """想一次, 做一批, 直到停.

        四条"追加点东西再来一次"的路 (格式重试, 溢出强压, 工作区变更, 收工具) 都是
        回到 while 顶上, 不递归.
        """
        while True:
            self._assemble(assemble)
            evicted_before = self._window.evicted_count
            stop = self._rules.before_model(self._view, self._apply_rewrite)
            if isinstance(stop, LoopStop):
                return self._stop(stop)
            if self._window.evicted_count != evicted_before:
                # 刚压缩过: 旧通知随被淘汰的那段一起没了, 再组一次, 该重注入的这次会带
                # 回来 (ADR-0056 决策 9). 会话段也在这一次重算.
                self._assemble(assemble)

            outcome = self._call_model()
            if isinstance(outcome, LoopStop):
                return self._stop(outcome)
            if isinstance(outcome, Reask | Rewrite):
                self._apply_retry(outcome)
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
            if not outcome.tool_calls:
                self._answer = outcome.text
                self._events.next_step()
                self._events.decision("模型已产出最终回答")
                return self._stop(LoopStop(LoopStopReason.FINAL_ANSWER))

            batch_stop = self._run_batch(outcome.tool_calls, execute)
            if batch_stop is not None:
                return batch_stop
            # 这批做完了 (或者目录收了), 回去让模型接着想.

    def _assemble(self, assemble: ContextAssembly) -> None:
        """组一次材料, 把这次新出的通知追加进窗口.

        通知只在这里进窗口: 一批工具结果已经全部回填, 或者用户消息刚进来. 不会插在
        tool call 与它的 tool result 之间.
        """
        self._context = assemble(self._window)
        if self._context.notices:
            self._append(*self._context.notices)
        _log.debug(
            "loop.context",
            static_sha=self._context.fingerprints.static[:12],
            session_sha=self._context.fingerprints.session[:12],
            tools=[schema.name for schema in self._tools],
            notices=[item.notice_kind for item in self._context.notices],
        )

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

    def _run_batch(
        self, calls: tuple[ToolCall, ...], execute: ToolExecutor
    ) -> LoopStop | None:
        """把模型这次要的工具整批交给执行链, 结果按请求序回填.

        分批与并发在执行链里 (ADR-0054 决策 4), 循环只看到"一批进, 一批出". 规则仍然
        逐个看: before_dispatch 先把要挡的挡掉, 剩下的整批执行, 再逐个 after_observe.

        返回 None 表示这批做完了, 该回去调模型; 返回 LoopStop 表示本轮到此为止.
        """
        self._pending_calls = list(calls)
        # 模型这一轮请求的调用已经全部确定. 先把整批意图发给观察端, 页面才能在任何
        # 工具真正启动之前画出完整队列.
        self._events.tool_batch(calls)
        to_run: list[ToolCall] = []
        for call in calls:
            verdict = self._rules.before_dispatch(call, self._view())
            if isinstance(verdict, LoopStop):
                return self._stop(verdict)
            if isinstance(verdict, Deny):
                self._pending_calls.remove(call)
                self._deny(call, verdict)
                continue
            to_run.append(call)
        observations = self._execute(tuple(to_run), execute)
        # 执行过的调用结果先全部回填: 协议要求每个 tool_call 都有配对的结果, 规则的
        # 处置 (停, 收工具) 不能让已经执行了的调用没有结果.
        self._pending_calls = []
        for call, observation in zip(to_run, observations, strict=True):
            self._append(
                _tool_result(
                    call,
                    labelled(call, observation.content),
                    is_error=observation.is_error,
                )
            )
        for call, observation in zip(to_run, observations, strict=True):
            after = self._rules.after_observe(call, observation, self._view())
            if isinstance(after, LoopStop):
                return self._stop(after)
            if isinstance(after, CloseTools):
                self._close_tools(after.notice)
                return None
            if isinstance(after, Inject):
                self._deferred.append(after)
        # 整批工具结果都回填完了, 攒着的消息现在才能写: 插在两条工具结果中间会打断
        # 配对的连续区, 供应商会拒.
        for inject in self._deferred:
            self._append(*inject.messages)
            self._events.decision(inject.summary)
        self._deferred = []
        return None

    def _execute(
        self, calls: tuple[ToolCall, ...], execute: ToolExecutor
    ) -> tuple[LoopObservation, ...]:
        """交给外层执行一批工具, 拿回同样多的结论. 循环只认识 `execute` 这一个函数."""
        if not calls:
            return ()
        for call in calls:
            self._tool_calls += 1
            update_run_context(step=self._events.next_step(), tool=call.name)
            # 入参**原样**写进日志, 与发给终端的 scrub_arguments 是两条路: 屏幕上要防的
            # 是一屏 base64 把过程刷没, 而排查一次"工具为什么这么干"必须看到它真正收到
            # 了什么.
            _log.info(
                "tool.requested",
                tool=call.name,
                tool_call_id=call.tool_call_id,
                arguments=call.arguments,
                tool_call_index=self._tool_calls,
            )
        observations = execute(calls)
        for call, observation in zip(calls, observations, strict=True):
            _log.info(
                "tool.observed",
                tool=call.name,
                is_error=observation.is_error,
                disposition=observation.disposition.value,
                chars=len(observation.content),
            )
            if _log.enabled_for_debug():
                _log.debug("tool.observed.content", content=observation.content)
        return observations

    def _deny(self, call: ToolCall, verdict: Deny) -> None:
        """规则挡下了这一个调用: 不执行, 把规则给的那段文字当它的工具结果回填.

        回填而不是静默跳过, 也不是直接停止本轮: 模型必须知道这个调用怎么了, 否则它
        只会原样再要一次. 这条消息进 transcript, 下一次模型调用就看得到.
        """
        self._append(_tool_result(call, verdict.notice, is_error=True))
        self._events.tool_rejected(call, notice=verdict.notice, code=verdict.code)

    def _close_tools(self, notice: str) -> None:
        """收掉本轮的工具目录, 逼模型给出最终回答.

        不直接停: 人有权知道模型原本想做什么, 以及为什么停下. 硬中止的话屏幕上只会
        突然什么都没有. 收完由 `_run_batch` 跳出这批, 回到调模型.
        """
        _log.warning(
            "loop.tools_closed",
            notice=notice,
            abandoned=[call.name for call in self._pending_calls],
        )
        self._abandon_pending(notice)
        self._deferred = []
        self._tools_closed = True
        self._append(guidance(notice))
        self._events.decision(notice)

    def _abandon_pending(self, notice: str) -> None:
        """给还在排队的调用补上 tool result, 并清空队列.

        协议要求 assistant 消息里每个 tool_call 都有对应的 tool result: 直接丢掉它们,
        下一次请求就是残缺的, 供应商会拒. 所以是"标记为未执行", 不是"当作没发生过".
        """
        for call in self._pending_calls:
            detail = prompts.loop_abandoned_call(notice)
            self._append(_tool_result(call, detail, is_error=True))
            self._events.tool_abandoned(call, notice=detail)
        self._pending_calls = []

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
            # 窗口就是全部: 通知已经在里面 (ADR-0056 决策 2), 没有额外要拼的帧.
            messages=self._messages,
            params=ModelParams(),
            system_prompt=(
                self._context.system_prompt if self._context is not None else None
            ),
            tools=self._tools,
            cancel_token=self._new_cancel_token(),
        )

    def _stop(self, stop: LoopStop) -> LoopStop:
        """本轮的唯一出口. 排队中的调用在这里补上"未执行"的配对结果."""
        if self._pending_calls:
            # 计划评审是今天唯一带着队列停下的路; 其余停止发生在队列排空之后.
            notice = (
                prompts.loop_review_abandon()
                if stop.reason is LoopStopReason.WAIT_PLAN_REVIEW
                else prompts.loop_abandoned_call(stop.reason.value)
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


def _tool_result(call: ToolCall, content: str, *, is_error: bool) -> ChatMessage:
    """一条配对的工具结果消息. 三处写法只该有一份."""
    return ChatMessage(
        role=MessageRole.TOOL,
        content=(
            ToolResultBlock(
                tool_call_id=call.tool_call_id, content=content, is_error=is_error
            ),
        ),
    )
