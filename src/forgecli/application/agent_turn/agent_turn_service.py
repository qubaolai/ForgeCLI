"""AgentTurnService：一轮自然语言对话的 application 用例（ADR-0010 §3 / §5）。

2026-07-24 起本类驱动 AgentLoop（MVP 实现 BuiltinAgentLoop）：service 是唯一执行
副作用的 application service——事件成对落盘、usage 草稿写入、观察回填都在这里；
loop 只产出结构化意图（LoopAction / LoopStop）。

约束（ADR-0003 / 概要设计 §6.5）：service 不依赖任何界面库；
**所有事件落盘只经 SessionService 单一门面**，本类不持有 EventStore/StateStore。

一轮 = 一个 turn：成对写 user_message / assistant_message，共享 turn_id；
turn 的终态写在 assistant_message 上（COMPLETED；异常与阻塞停止隔离为 FAILED）。
取消轮（Ctrl-C）落盘已收到的部分流式文本 + 取消标记，payload 带
stop_reason=user_cancelled，事件日志无歧义。

内存 transcript 镜像事件重放：user/assistant 成对文本全部进入历史（取消标记随
文本可见，错误轮的可行动文案也让模型知道上轮失败）。resume 时从历史事件回填。
"""

from __future__ import annotations

import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.context.assembler import ContextAssembler
from forgecli.application.context.runtime_facts import RuntimeFacts
from forgecli.application.context.window_manager import WindowManager
from forgecli.application.memory.memory_service import MemoryService
from forgecli.application.planning.planning_service import (
    PlanningService,
)
from forgecli.application.prompt.project_instruction_reader import (
    ProjectInstructionReader,
)
from forgecli.application.prompt.system_prompt_builder import SystemPromptBuilder
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.application.session.session_service import SessionService
from forgecli.application.tool_request.dispatcher import (
    CoordinatorToolDispatcher,
)
from forgecli.application.tool_request.observations import ObservationKind
from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopObservation,
    LoopStop,
    ObservationSource,
    ToolRequestAction,
)
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    PlanProposedPayload,
    RunEventPayload,
    TodoUpdatedPayload,
    TurnFinishedPayload,
)
from forgecli.domain.agent.state import AssembledContext, LoopInput
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.context.compaction import CompactionDraft, CompactionLevel
from forgecli.domain.context.window import Window
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import (
    AssistantResponse,
    MessageRole,
    TurnPause,
    TurnStatus,
)
from forgecli.domain.intents import InputOrigin, SessionMode
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.session.events import EventType, SessionEvent
from forgecli.shared.observability.context import bind
from forgecli.shared.observability.log import get_log

_log = get_log(__name__)

# 单轮驱动的安全步数上限. 工具调用开放后一轮会跑很多步 (每次工具调用都是一次
# observe), 因此按循环自己的硬上限走, 这个常量只是兜底.
_DEFAULT_MAX_LOOP_STEPS = 99999


@dataclass(frozen=True)
class _TurnOutcome:
    """一轮驱动的内部结果：文本 + 终态 + 待落盘 usage 草稿 + 机器可读停止标记。"""

    text: str
    status: TurnStatus
    usage_drafts: tuple[UsageRecordDraft, ...] = ()
    compaction_drafts: tuple[CompactionDraft, ...] = ()
    # 循环跑完时的窗口 (ADR-0041 决策 4). 它已经含这一轮的助手回复 —— 循环的
    # _remember_assistant 把每次模型输出都写了进去. 驱动抛错那条路上循环没交出来,
    # 那时为空, 由 _remember_turn 退回"至少把用户这句话记下"的兜底.
    window: tuple[ChatMessage, ...] = ()
    stop_reason: str | None = None
    # 完整 traceback. 只进事件日志, 不上屏 —— 终端一行摘要就够,
    # 但排查时必须找得回来.
    diagnostic: str | None = None
    # 本轮停下来等人做什么 (ADR-0023). 交回 CLI 由它驱动交互.
    pause: TurnPause | None = None


class AgentTurnService:
    """处理一轮自然语言输入：驱动 AgentLoop 并成对落盘会话事件。"""

    def __init__(
        self,
        session: SessionService,
        *,
        loop_factory: Callable[[], BuiltinAgentLoop],
        # 提示词三件套是**必填**: 缺提示词的主 Agent 不知道自己是谁, 有哪些工具, 也不
        # 知道自己在什么平台上 (ADR-0018 §11). 给个默认值就等于允许静默降级回接入之前
        # 的状态, 而那种降级不会报错, 只会让模型开始猜.
        prompt_builder: SystemPromptBuilder,
        runtime_facts: Callable[[], RuntimeFacts],
        instructions: ProjectInstructionReader,
        # 每轮现取, 与 runtime_facts 同一个形状: 用户可能刚 /model 换过模型, 上一轮的
        # 窗口不作数. 返回 None 表示这一轮算不出窗口 (目录里没有这个模型), 那时不压缩
        # 也不猜 —— 猜小了平白压掉内容, 猜大了等于没有这道防线 (ADR-0032 决策 1).
        context_budget: Callable[[], ContextBudget | None],
        # 与循环持有的是**同一个**实例. /compact 走的是这一份, 自动压缩走循环那一份,
        # 但两条路必须用同一套判据与同一个 ArtifactStore.
        context: WindowManager | None = None,
        # 记忆的唯一入口 (ADR-0033 决策 10). 给这里而不是给循环: ADR-0010 明写循环
        # 不读写长期记忆, 而功能上也用不着 —— 读只发生在 _assemble_context 那一刻,
        # 与 ProjectInstructionReader 和 PlanningService 完全同构.
        memory: MemoryService | None = None,
        planning: PlanningService | None = None,
        run_bus: AgentRunEventBus | None = None,
        tools: CoordinatorToolDispatcher | None = None,
        max_steps: int = _DEFAULT_MAX_LOOP_STEPS,
    ) -> None:
        self._session = session
        # 组装走 ContextAssembler: 六层的取材规则只该有一份.
        self._assembler = ContextAssembler(prompt_builder)
        # 每轮现取: 用户可能刚 /add-dir 加过根, 上一轮的事实不作数.
        self._runtime_facts = runtime_facts
        self._instructions = instructions
        self._context_budget = context_budget
        self._context = context
        # 缺省为 None: 没接记忆时提示词块整块不渲染, 链路照常工作.
        self._memory = memory
        # 计划与待办缺省为 None: 没接时两个提示词块整块不渲染, 链路照常工作.
        self._planning = planning
        # 计划与待办的运行事件发在这里 (ADR-0022 §7): 服务独家持有 turn_id 且是唯一的
        # 驱动方. 换成协调器发, 就得让协调器认识"plan_write 这个名字意味着要发事件".
        self._run_bus = run_bus
        # 每 turn 经工厂取新 loop 实例（BuiltinAgentLoop 持有 per-turn 状态）。
        self._loop_factory = loop_factory
        # 工具分发器缺省为 None: 没接工具时循环产出 ToolRequestAction 会得到一条明确的
        # "工具不可用" observation, 而不是被静默忽略.
        self._tools = tools
        self._max_steps = max_steps
        self._turns = 0
        self._window = Window()

    def resume(self, history: Iterable[SessionEvent]) -> None:
        """续写一段历史会话: 接回 turn 计数, 并从事件重建会话窗口."""
        events = list(history)
        self._turns = sum(1 for e in events if e.type == EventType.USER_MESSAGE)
        self._window = _rebuild_window(events)

    def handle_user_message(
        self, text: str, *, origin: InputOrigin = InputOrigin.PROGRAM
    ) -> AssistantResponse:
        self._turns += 1
        turn_id = f"turn_{self._turns:04d}"
        session_id = self._session.current().session_id
        with bind(session_id=session_id, turn_id=turn_id):
            return self._handle_bound(text, origin, turn_id)

    def _handle_bound(
        self, text: str, origin: InputOrigin, turn_id: str
    ) -> AssistantResponse:
        """本轮全部日志都带 session / turn: 一次故障能顺着 turn_id 一路读到底."""
        _log.info(
            "turn.received",
            origin=origin.value,
            chars=len(text),
            text=text,
            window_messages=len(self._window.messages),
        )
        self._session.record_user_message(text, turn_id=turn_id, origin=origin)
        if self._memory is not None:
            # 记一次"当前是哪一轮", 供本轮内 memory_write 取来源 (ADR-0033 决策 7).
            # 工具的 perform 拿不到会话身份, 而把它塞进 normalized_input 会进
            # plan_hash, 破掉"内容相同的调用哈希相同"那条不变量.
            self._memory.begin_turn(
                session_id=self._session.current().session_id, turn_id=turn_id
            )
        # mode 从 session 快照读，单一真相（不再依赖 REPL 内存态）。
        mode = self._session.current().mode
        with _log.span("turn", mode=mode.value) as span:
            outcome = self._obtain_outcome(text, mode, turn_id)
            span.set(
                status=outcome.status.value,
                stop_reason=outcome.stop_reason,
                answer_chars=len(outcome.text),
                usage_records=len(outcome.usage_drafts),
                compactions=len(outcome.compaction_drafts),
                pause=None if outcome.pause is None else outcome.pause.value,
            )
        _log.info("turn.answer", text=outcome.text)
        self._session.record_assistant_message(
            outcome.text,
            turn_id=turn_id,
            status=outcome.status,
            stop_reason=outcome.stop_reason,
            diagnostic=outcome.diagnostic,
        )
        for draft in outcome.usage_drafts:
            # usage 写入边界（ADR-0011 §11.1）：loop 只随回复交回草稿，这里统一落盘。
            self._session.record_usage(draft.to_payload(), turn_id=turn_id)
        for compaction in outcome.compaction_drafts:
            # 同一条分工 (ADR-0032 决策 1): 循环压缩, 但不写事件.
            self._session.record_compaction(compaction.to_payload(), turn_id=turn_id)
            _log.info(
                "context.compacted",
                level=compaction.level.value,
                tokens_saved=compaction.tokens_saved,
            )
        self._remember_turn(text, outcome)
        return AssistantResponse(
            turn_id=turn_id,
            text=outcome.text,
            status=outcome.status,
            pause=outcome.pause,
        )

    # ---- 提示词 ----

    def _assemble_context(self, mode: SessionMode) -> AssembledContext:
        """按变更源把本轮上下文分层组装 (ADR-0041 决策 1).

        任一步都不调模型也不执行工具: 上下文必须在第一次模型调用之前就已经定死
        (ADR-0018 §6.1).

        三层的读取时机各不相同, 而这正是它们分层的理由:

        - [2][3] 提示词只依赖包版本与 FORGE.md, builder 内部按进程缓存内置五块;
        - [4] 运行事实每轮现取 —— 用户可能刚 /add-dir 加过根, 上一轮的事实不作数;
        - [6] 计划, 待办与记忆同样每轮现读: 待办的价值就在于它反映**此刻**的执行状态,
          而模型上一轮刚用 todo_set_status 打过勾.
        """
        facts = self._runtime_facts()
        assembled = self._assembler.assemble(
            mode=mode,
            facts=facts,
            # 本轮读一次. 工具在本轮改了 FORGE.md, 新内容从下一轮生效 (ADR-0018 §6.2).
            instructions=self._instructions.read(facts.workspace_roots),
            planning=None if self._planning is None else self._planning.load(),
            memory=() if self._memory is None else self._memory.load(),
            budget=self._context_budget(),
            # 真围栏, 不是 None: 不给的话"自动放行"那一行报的是保守基线, 比这一档真实的
            # 边界窄, 模型会以为每一步都得先问人.
            fence=None if self._tools is None else self._tools.fence_for(mode),
        )
        policy = assembled.policy
        _log.info(
            "context.assembled",
            version=policy.version,
            fingerprint=policy.fingerprint,
            blocks=len(policy.blocks),
            policy_chars=len(policy.text),
            runtime_chars=len(assembled.runtime_context),
            state_chars=len(assembled.state_frame),
            cwd=facts.working_directory,
        )
        # 整段只在 debug 下写: 它每轮几千字, info 级别会把日志淹掉, 而"模型到底看到了
        # 什么"恰恰是最需要能翻出来的一件事.
        _log.debug("context.system_prompt", text=assembled.system_prompt)
        _log.debug("context.state_frame", text=assembled.state_frame)
        return assembled

    # ---- 内部 ----

    def _obtain_outcome(
        self, text: str, mode: SessionMode, turn_id: str
    ) -> _TurnOutcome:
        try:
            return self._run_loop(text, mode, turn_id)
        except Exception as error:
            # 失败隔离: 驱动抛错不破坏会话, 仍成对落盘 assistant.
            #
            # 但**不能连异常一起丢掉**. 早先这里是裸 `except Exception:`, 连异常对象都
            # 不绑定, 用户只看到"助手处理出错。"而 traceback 无处可寻 —— 一个平台相关
            # 的失败因此完全无法定位, 连"错在哪一层"都答不上来.
            #
            # 分两处给: 摘要进用户可见文本 (类型 + 消息, 一行), 完整 traceback 进
            # assistant_message 事件的 diagnostic 字段. 后者不上屏, 但 /resume 与事故
            # 排查时读 events.jsonl 就能拿到.
            _log.exception(
                "turn.driver_failed",
                error=type(error).__name__,
                message=str(error),
            )
            # **运行事件流也要收尾.** 循环的 `_publish_turn_finished` 只在 `_stop()` 里
            # 调, 而抛异常根本走不到那儿 —— 于是会话事件成对落了盘, 而页面听的那条流
            # 没有终态, 一直显示"处理中"直到用户自己刷新。
            #
            # 实测过一次: 一个 NameError 让整轮在第一次模型调用就炸了, 后端如实记了
            # turn.driver_failed 并写了 assistant 事件, 前端却一直转圈。
            self._publish_run(
                AgentRunEventKind.TURN_FAILED,
                TurnFinishedPayload(
                    status="driver_failed",
                    detail=f"{type(error).__name__}: {error}",
                ),
                turn_id,
            )
            return _TurnOutcome(
                text=f"助手处理出错: {type(error).__name__}: {error}",
                status=TurnStatus.FAILED,
                diagnostic="".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                ),
            )

    def _run_loop(self, text: str, mode: SessionMode, turn_id: str) -> _TurnOutcome:
        loop = self._loop_factory()
        # 目录按**当前**模式现算: 用户可能刚用 Tab 切过档, 上一轮的目录不作数.
        # 没装工具时为 None, 循环因此不会给模型任何可调用的工具.
        #
        # 只算一次: catalog_for 每次调用都会新建一个 ExecutionContext, 算两次可能得到
        # 两份不同的快照, 而快照哈希进审计.
        catalog = None if self._tools is None else self._tools.catalog_for(mode)
        assembled = self._assemble_context(mode)
        step = loop.start(
            LoopInput(
                turn_id=turn_id,
                session_id=self._session.current().session_id,
                mode=mode,
                context=replace(
                    assembled,
                    initial_window=(
                        *self._window.messages,
                        ChatMessage(role=MessageRole.USER, content=(TextBlock(text),)),
                    ),
                ),
                tool_catalog=catalog,
            )
        )
        answer: str | None = None
        for _ in range(self._max_steps):
            if isinstance(step, LoopStop):
                return self._outcome_from_stop(step, answer, loop)
            action = step
            if isinstance(action, AnswerAction):
                answer = action.text
                step = loop.observe(
                    LoopObservation(
                        content="answer_delivered",
                        source=ObservationSource.CONTEXT,
                    )
                )
            elif isinstance(action, ToolRequestAction):
                step = loop.observe(
                    self._run_tool_and_watch_planning(action, text, mode, turn_id)
                )
            elif action is None:
                # 纯反思步：无动作可执行，直接回喂空观察继续。
                step = loop.observe(
                    LoopObservation(content="noop", source=ObservationSource.CONTEXT)
                )
            else:
                # LoopAction 只有 answer 与 request_tool 两种 (ADR-0043 决策 1):
                # 审批走 ApprovalService, 压缩走 WindowManager, 提问走 ask_user 工具.
                # 走到这里说明有人加了第三种动作却没接上驱动.
                return _TurnOutcome(
                    text=render_notice("stop.unsupported_action"),
                    status=TurnStatus.FAILED,
                    usage_drafts=loop.usage_drafts,
                    compaction_drafts=loop.compaction_drafts,
                    window=loop.window,
                )
        return _TurnOutcome(
            text=render_notice("stop.loop_steps_exceeded"),
            status=TurnStatus.FAILED,
            usage_drafts=loop.usage_drafts,
            compaction_drafts=loop.compaction_drafts,
            window=loop.window,
        )

    def _run_tool_and_watch_planning(
        self,
        action: ToolRequestAction,
        user_text: str,
        mode: SessionMode,
        turn_id: str,
    ) -> LoopObservation:
        """跑一次工具, 顺带比对计划与待办的 revision, 变了就发事件.

        **按 revision 比对, 不按工具名判断** (ADR-0022 §7). 换成"看到 plan_write 就发
        PLAN_CREATED"的话, 这一层就认识了工具名, 而将来任何一条别的路径改了计划 (斜杠
        命令, 恢复, 将来的子 Agent) 都不会有事件.

        服务本来就独家持有 turn_id 且是唯一的驱动方, 所以事件发在这里而不是协调器.
        """
        before = self._planning_revisions()
        observation = self._run_tool(action, user_text, mode, turn_id)
        self._emit_planning_changes(before, turn_id)
        return observation

    def _planning_revisions(
        self,
    ) -> tuple[tuple[str, int] | None, tuple[str, int] | None]:
        """(计划身份, 待办身份).

        身份用 (id, revision): 换一份计划和改一份计划都要算作变化.
        """
        if self._planning is None:
            return (None, None)
        active = self._planning.load()
        plan = (
            None if active.plan is None else (active.plan.plan_id, active.plan.revision)
        )
        todo = (
            None if active.todo is None else (active.todo.todo_id, active.todo.revision)
        )
        return (plan, todo)

    def _emit_planning_changes(
        self,
        before: tuple[tuple[str, int] | None, tuple[str, int] | None],
        turn_id: str,
    ) -> None:
        if self._planning is None:
            return
        active = self._planning.load()
        previous_plan, previous_todo = before
        if active.plan is not None:
            current = (active.plan.plan_id, active.plan.revision)
            if current != previous_plan:
                plan = active.plan
                # payload 只记摘要与引用. 正文的真相源是 plans/ 下的文件, 复制一份进
                # 事件流就有了两个会漂移的副本, 而事件流 append-only, 漂了改不回来.
                self._session.record_tool_event(
                    EventType.PLAN_CREATED,
                    {
                        "plan_id": plan.plan_id,
                        "revision": plan.revision,
                        "template_version": plan.template_version,
                        "title": plan.title,
                        "step_count": plan.step_count,
                        "file": f"{plan.plan_id}/r{plan.revision}.json",
                    },
                    turn_id=turn_id,
                )
                self._publish_run(
                    AgentRunEventKind.PLAN_PROPOSED,
                    PlanProposedPayload(
                        plan_id=plan.plan_id,
                        title=plan.title,
                        revision=plan.revision,
                        step_count=plan.step_count,
                    ),
                    turn_id,
                )
        if active.todo is not None:
            current_todo = (active.todo.todo_id, active.todo.revision)
            if current_todo != previous_todo:
                todo = active.todo
                self._session.record_tool_event(
                    EventType.TODO_UPDATED,
                    {
                        "todo_id": todo.todo_id,
                        "plan_id": todo.plan_id,
                        "revision": todo.revision,
                        "done": todo.done_count,
                        "total": todo.total_count,
                    },
                    turn_id=turn_id,
                )
                self._publish_run(
                    AgentRunEventKind.TODO_UPDATED,
                    TodoUpdatedPayload(
                        todo_id=todo.todo_id,
                        done=todo.done_count,
                        total=todo.total_count,
                        current="" if todo.current is None else todo.current.title,
                    ),
                    turn_id,
                )

    def _publish_run(
        self, kind: AgentRunEventKind, payload: RunEventPayload, turn_id: str
    ) -> None:
        """没接总线就是没人看. 展示缺席不影响状态推进 (ADR-0016 §4.3)."""
        if self._run_bus is None:
            return
        self._run_bus.publish(kind, turn_id=turn_id, payload=payload)

    def _run_tool(
        self,
        action: ToolRequestAction,
        user_text: str,
        mode: SessionMode,
        turn_id: str,
    ) -> LoopObservation:
        """把工具请求交给安全管线, 把结论作为 observation 回填.

        这是 AgentLoop 触达真实世界的**唯一**路径: 服务本身不认识 ToolRegistry, 也
        拿不到 ToolRuntime, 所以不存在"绕过协调器直接执行"这条分支.
        """
        if self._tools is None:
            return LoopObservation(
                # 前缀取自 ObservationKind, 不手写: 手写一遍就是第二份会漂的格式.
                content=(
                    f"[{ObservationKind.TOOL_UNAVAILABLE.value}] "
                    f"{render_notice("loop.tools_not_wired")}"
                ),
                source=ObservationSource.ERROR,
                is_error=True,
            )
        observation = self._tools.dispatch(
            action.request,
            mode=mode,
            session_id=self._session.current().session_id,
            turn_id=turn_id,
            user_intent_summary=user_text,
        )
        return observation.to_loop_observation()

    def _outcome_from_stop(
        self, stop: LoopStop, answer: str | None, loop: BuiltinAgentLoop
    ) -> _TurnOutcome:
        drafts = loop.usage_drafts
        compactions = loop.compaction_drafts
        if stop.reason is LoopStopReason.FINAL_ANSWER:
            if answer is None:
                return _TurnOutcome(
                    text=render_notice("stop.no_answer"),
                    status=TurnStatus.FAILED,
                    usage_drafts=drafts,
                    compaction_drafts=compactions,
                    window=loop.window,
                    stop_reason=stop.reason.value,
                )
            return _TurnOutcome(
                text=answer,
                status=TurnStatus.COMPLETED,
                usage_drafts=drafts,
                compaction_drafts=compactions,
                window=loop.window,
                stop_reason=stop.reason.value,
            )
        if stop.reason is LoopStopReason.WAIT_PLAN_REVIEW:
            # 本轮是成功的: 模型交出了一份计划. 停下来是它主动要的, 不是出错.
            #
            # text 取模型这一轮说过的话 (通常是一句"我拟了个方案"), 计划正文由 CLI 从
            # PlanningService 现取 —— 让它跟着回复文本走, 就会有两份可能不一致的正文.
            return _TurnOutcome(
                text=answer or render_notice("stop.review_notice"),
                status=TurnStatus.COMPLETED,
                usage_drafts=drafts,
                compaction_drafts=compactions,
                window=loop.window,
                stop_reason=stop.reason.value,
                pause=TurnPause.PLAN_REVIEW,
            )
        if stop.reason is LoopStopReason.USER_CANCELLED:
            partial = loop.partial_answer or None
            notice = render_notice("stop.cancel_notice")
            text = f"{partial}\n{notice}" if partial else notice
            return _TurnOutcome(
                text=text,
                status=TurnStatus.FAILED,
                usage_drafts=drafts,
                compaction_drafts=compactions,
                window=loop.window,
                stop_reason=stop.reason.value,
            )
        return _TurnOutcome(
            text=stop.message or render_notice("stop.model_call_failed"),
            status=TurnStatus.FAILED,
            usage_drafts=drafts,
            compaction_drafts=compactions,
            window=loop.window,
            stop_reason=stop.reason.value,
        )

    def _remember_turn(self, text: str, outcome: _TurnOutcome) -> None:
        """把循环跑完的窗口接过来 (ADR-0041 决策 4).

        原先这里是**重建**一份跨轮历史: 用户原话 + 一行工具结论 + 助手回复, 工具结果整个
        不跨回合. 那是在一条结果还带着几 KB 正文时的取舍.

        ADR-0041 决策 6 之后正文本来就不进窗口, 一条结果只剩摘要与结构化字段, 于是原样
        留着比重述一遍便宜也准确. 更要紧的是: 窗口只追加, 而"跨轮重建一份不一样的历史"
        本身就是一次中段改写 —— 上一轮发出去的前缀, 下一轮对不上了, 缓存全丢.

        助手回复**通常已经在窗口里了**: 循环的 `_remember_assistant` 把每一次模型输出都
        写了进去, 最后那次就是这一轮的回答. 只有收摊路径 (步数用尽, 上下文压不下去,
        用户取消) 的 `outcome.text` 是一句 Forge 自己拼的通知, 那种才要补.

        所以这里按"末条是不是已经是这句话"判, 而不是无条件追加 —— 无条件追加会让每一轮
        的回答在跨轮窗口里出现两次, 越往后重复越多.
        """
        if outcome.window:
            messages = outcome.window
        else:
            # 驱动抛错时循环没交出窗口. 至少把用户这句话记下来, 否则下一轮它就不见了.
            messages = (
                *self._window.messages,
                ChatMessage(role=MessageRole.USER, content=(TextBlock(text),)),
            )
        if outcome.text and not _ends_with_assistant_text(messages, outcome.text):
            messages = (
                *messages,
                ChatMessage(
                    role=MessageRole.ASSISTANT, content=(TextBlock(outcome.text),)
                ),
            )
        self._window = self._window.replace_messages(messages)


def _ends_with_assistant_text(messages: tuple[ChatMessage, ...], text: str) -> bool:
    """末条是不是已经是这段助手文本."""
    if not messages:
        return False
    last = messages[-1]
    if last.role is not MessageRole.ASSISTANT:
        return False
    return text in [
        block.text for block in last.content if isinstance(block, TextBlock)
    ]


def _rebuild_window(events: list[SessionEvent]) -> Window:
    """从历史事件重建会话窗口.

    淘汰点是**按位置**生效的: 读到一条 CONTEXT_COMPACTED 就丢掉已经累积的部分, 从交接
    说明接着往下走. 事件在日志里的位置本身定义了它覆盖的范围, 所以不需要另存起止 id.

    原始事件一条不删, 只是重建时不再重放它们 —— 淘汰影响的是"下一轮发给模型的窗口",
    不是审计. 排查时读全量 events.jsonl 仍然看得到当初发生的一切.

    重建出来的窗口**没有工具结果**: 事件流里只有用户与助手的文本, TOOL_COMPLETED 的
    payload 是机制事实, 不含回填给模型的那一段. `/resume` 因此会丢掉上一次会话的工具
    往返细节 —— 那是既有行为, 不是本次引入的.

    唯一的例外是 ask_user 的回答 (ADR-0043 决策 12): 那是人写的字, 不可复现, 丢了就再也
    找不回来. 它在会话内是一条 tool result, 这里换成一条用户消息 —— 形状不一致, 但比丢掉
    强.
    """
    transcript: list[ChatMessage] = []
    for event in events:
        if event.type == EventType.USER_QUESTION_ANSWERED:
            transcript.append(
                ChatMessage(
                    role=MessageRole.USER,
                    content=(
                        TextBlock(
                            render_notice(
                                "ask_user.answer_replay",
                                question=str(event.payload.get("question", "")),
                                answer=str(event.payload.get("answer", "")),
                            )
                        ),
                    ),
                )
            )
            continue
        if event.type == EventType.CONTEXT_COMPACTED:
            summary = _summary_of(event)
            if summary:
                transcript = [
                    ChatMessage(
                        role=MessageRole.USER,
                        content=(
                            TextBlock(
                                f"{render_notice("context.compaction_header")}\n\n{summary}"
                            ),
                        ),
                    )
                ]
            continue
        text = str(event.payload.get("text", ""))
        if not text:
            continue
        if event.type == EventType.USER_MESSAGE:
            transcript.append(
                ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))
            )
        elif event.type == EventType.ASSISTANT_MESSAGE:
            transcript.append(
                ChatMessage(role=MessageRole.ASSISTANT, content=(TextBlock(text),))
            )
    return Window(messages=tuple(transcript))


def _summary_of(event: SessionEvent) -> str:
    """一条压缩事件里的摘要正文; 不是 SUMMARY 级则为空."""
    if str(event.payload.get("level", "")) != CompactionLevel.SUMMARY.value:
        return ""
    return str(event.payload.get("summary", ""))
