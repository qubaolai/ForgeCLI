"""AgentTurnService：一轮自然语言对话的 application 用例（ADR-0010 §3 / §5）。

2026-07-24 起本类驱动 AgentLoop（MVP 实现 BuiltinAgentLoop）：service 是唯一执行
副作用的 application service——事件成对落盘、usage 草稿写入、观察回填都在这里；
loop 只产出结构化意图（LoopDecision / LoopAction / LoopStop）。

约束（ADR-0003 / 概要设计 §6.5）：service 不依赖 Rich/Typer/prompt_toolkit；
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
from dataclasses import dataclass

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.manual_shell.mutation_barrier import ManualMutationBarrier
from forgecli.application.planning import ActivePlanning, PlanningService
from forgecli.application.prompt.project_instruction_reader import (
    ProjectInstructionReader,
)
from forgecli.application.prompt.runtime_facts import RuntimeFacts
from forgecli.application.prompt.system_prompt_builder import (
    PromptBuildInput,
    SystemPromptBuilder,
    ToolBrief,
)
from forgecli.application.session import SessionService
from forgecli.application.tool_request.dispatcher import (
    CoordinatorToolDispatcher,
)
from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopObservation,
    LoopStop,
    ObservationSource,
    ToolRequestAction,
)
from forgecli.domain.agent.prompt import PromptSnapshot
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    PlanProposedPayload,
    RunEventPayload,
    TodoUpdatedPayload,
)
from forgecli.domain.agent.state import ContextPackage, LoopInput
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import (
    AssistantResponse,
    MessageRole,
    TurnPause,
    TurnStatus,
)
from forgecli.domain.intents import InputOrigin, SessionMode, UserMessage
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.session.events import EventType, SessionEvent
from forgecli.domain.tool.catalog import ToolCatalog

# 单轮驱动的安全步数上限. 工具调用开放后一轮会跑很多步 (每次工具调用都是一次
# observe), 因此按 LoopBudgets 给的预算走, 这个常量只是兜底.
_DEFAULT_MAX_LOOP_STEPS = 99999

_CANCEL_NOTICE = "（本轮回复已被用户取消）"

_REVIEW_NOTICE = "已提交一份计划, 等待你的决定。"


@dataclass(frozen=True)
class _TurnOutcome:
    """一轮驱动的内部结果：文本 + 终态 + 待落盘 usage 草稿 + 机器可读停止标记。"""

    text: str
    status: TurnStatus
    usage_drafts: tuple[UsageRecordDraft, ...] = ()
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
        planning: PlanningService | None = None,
        run_bus: AgentRunEventBus | None = None,
        tools: CoordinatorToolDispatcher | None = None,
        barrier: ManualMutationBarrier | None = None,
        max_steps: int = _DEFAULT_MAX_LOOP_STEPS,
    ) -> None:
        self._session = session
        self._prompt_builder = prompt_builder
        # 每轮现取: 用户可能刚 /add-dir 加过根, 上一轮的事实不作数.
        self._runtime_facts = runtime_facts
        self._instructions = instructions
        # 计划与待办缺省为 None: 没接时两个提示词块整块不渲染, 链路照常工作.
        self._planning = planning
        # 计划与待办的运行事件发在这里 (ADR-0022 §7): 服务独家持有 turn_id 且是唯一的
        # 驱动方. 换成协调器发, 就得让协调器认识"plan.write 这个名字意味着要发事件".
        self._run_bus = run_bus
        # 每 turn 经工厂取新 loop 实例（BuiltinAgentLoop 持有 per-turn 状态）。
        self._loop_factory = loop_factory
        # 工具分发器缺省为 None: 没接工具时循环产出 ToolRequestAction 会得到一条明确的
        # "工具不可用" observation, 而不是被静默忽略.
        self._tools = tools
        # 人工 Shell 的失效屏障 (ADR-0017 §10). 缺省新建一个空屏障: 没接人工 Shell 时
        # 它永远不 trip, 也就永远不阻塞。
        self._barrier = barrier or ManualMutationBarrier()
        self._max_steps = max_steps
        self._turns = 0
        self._history: list[ChatMessage] = []

    def resume(self, history: Iterable[SessionEvent]) -> None:
        """续写一段历史会话：接回 turn 计数，并从事件回填内存 transcript。"""
        events = list(history)
        self._turns = sum(1 for e in events if e.type == EventType.USER_MESSAGE)
        self._history = _rebuild_transcript(events)

    def handle_user_message(
        self, text: str, *, origin: InputOrigin = InputOrigin.PROGRAM
    ) -> AssistantResponse:
        self._turns += 1
        turn_id = f"turn_{self._turns:04d}"
        if self._barrier.blocked:
            # 人工 Shell 回来后清缓存失败 (ADR-0017 §12). 清不掉就无法证明后续裁决基于
            # 当前事实, 而"基于过期事实的 Allow"正是这套机制要防的 —— 宁可让用户重启。
            # 仍然成对落盘: 这一轮确实发生过, 只是被拒绝了。
            self._session.record_user_message(text, turn_id=turn_id, origin=origin)
            refusal = self._barrier.block_reason
            self._session.record_assistant_message(
                refusal, turn_id=turn_id, status=TurnStatus.FAILED
            )
            return AssistantResponse(
                turn_id=turn_id, text=refusal, status=TurnStatus.FAILED
            )
        self._session.record_user_message(text, turn_id=turn_id, origin=origin)
        # mode 从 session 快照读，单一真相（不再依赖 REPL 内存态）。
        mode = self._session.current().mode
        outcome = self._obtain_outcome(text, mode, turn_id)
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
        self._remember_turn(text, outcome)
        return AssistantResponse(
            turn_id=turn_id,
            text=outcome.text,
            status=outcome.status,
            pause=outcome.pause,
        )

    # ---- 提示词 ----

    def _compile_prompt(
        self, mode: SessionMode, catalog: ToolCatalog | None
    ) -> PromptSnapshot:
        """每轮开始时编译一次, 本轮不再重编 (ADR-0018 §6.1).

        任一步都不调模型也不执行工具: 提示词必须在第一次模型调用之前就已经定死.
        """
        facts = self._runtime_facts()
        return self._prompt_builder.build(
            PromptBuildInput(
                mode=mode,
                facts=facts,
                # 用途取工具自己的 title, 不在提示词层另写一份 —— 两份一定会漂.
                available_tools=(
                    ()
                    if catalog is None
                    else tuple(
                        ToolBrief(name=spec.name, title=spec.title)
                        for spec in catalog.entries
                    )
                ),
                # 本轮读一次. 工具在本轮改了 FORGE.md, 新内容从下一轮生效 (§6.2).
                project_instructions=self._instructions.read(facts.workspace_roots),
                # 同样每轮现读: 待办的全部价值就在于它反映**此刻**的执行状态, 而模型
                # 上一轮刚用 todo.set_status 打过勾.
                planning=(
                    ActivePlanning()
                    if self._planning is None
                    else self._planning.load()
                ),
            )
        )

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
        # 只算一次并同时交给 builder 与 LoopInput (ADR-0018 §2.1): catalog_for 每次调用
        # 都会新建一个 ExecutionContext, 算两次可能得到两份不同的快照, 于是提示词里写的
        # 工具与真正发给供应商的 schema 就对不上了.
        catalog = None if self._tools is None else self._tools.catalog_for(mode)
        step = loop.start(
            LoopInput(
                turn_id=turn_id,
                session_id=self._session.current().session_id,
                user_intent=UserMessage(raw_text=text),
                mode=mode,
                context_package=ContextPackage(
                    prompt=self._compile_prompt(mode, catalog),
                    messages=(
                        *self._history,
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
            action = step.next_action
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
                # ask_user / approval / compaction 属后续切片.
                return _TurnOutcome(
                    text="该动作类型尚未支持。",
                    status=TurnStatus.FAILED,
                    usage_drafts=_drafts_of(loop),
                )
        return _TurnOutcome(
            text="循环步数超出安全上限，本轮中止。",
            status=TurnStatus.FAILED,
            usage_drafts=_drafts_of(loop),
        )

    def _run_tool_and_watch_planning(
        self,
        action: ToolRequestAction,
        user_text: str,
        mode: SessionMode,
        turn_id: str,
    ) -> LoopObservation:
        """跑一次工具, 顺带比对计划与待办的 revision, 变了就发事件.

        **按 revision 比对, 不按工具名判断** (ADR-0022 §7). 换成"看到 plan.write 就发
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
                content="[tool_unavailable] 当前会话未装配工具系统",
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
        drafts = _drafts_of(loop)
        if stop.reason is LoopStopReason.FINAL_ANSWER:
            if answer is None:
                return _TurnOutcome(
                    text="循环未产出回复。",
                    status=TurnStatus.FAILED,
                    usage_drafts=drafts,
                    stop_reason=stop.reason.value,
                )
            return _TurnOutcome(
                text=answer,
                status=TurnStatus.COMPLETED,
                usage_drafts=drafts,
                stop_reason=stop.reason.value,
            )
        if stop.reason is LoopStopReason.WAIT_PLAN_REVIEW:
            # 本轮是成功的: 模型交出了一份计划. 停下来是它主动要的, 不是出错.
            #
            # text 取模型这一轮说过的话 (通常是一句"我拟了个方案"), 计划正文由 CLI 从
            # PlanningService 现取 —— 让它跟着回复文本走, 就会有两份可能不一致的正文.
            return _TurnOutcome(
                text=answer or _REVIEW_NOTICE,
                status=TurnStatus.COMPLETED,
                usage_drafts=drafts,
                stop_reason=stop.reason.value,
                pause=TurnPause.PLAN_REVIEW,
            )
        if stop.reason is LoopStopReason.USER_CANCELLED:
            partial = _partial_answer_of(loop)
            text = f"{partial}\n{_CANCEL_NOTICE}" if partial else _CANCEL_NOTICE
            return _TurnOutcome(
                text=text,
                status=TurnStatus.FAILED,
                usage_drafts=drafts,
                stop_reason=stop.reason.value,
            )
        return _TurnOutcome(
            text=stop.message or "模型调用失败。",
            status=TurnStatus.FAILED,
            usage_drafts=drafts,
            stop_reason=stop.reason.value,
        )

    def _remember_turn(self, text: str, outcome: _TurnOutcome) -> None:
        """把本轮写进内存 transcript：成对文本全部进入历史（镜像事件重放）。"""
        self._history.append(
            ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))
        )
        if outcome.text:
            self._history.append(
                ChatMessage(
                    role=MessageRole.ASSISTANT, content=(TextBlock(outcome.text),)
                )
            )


def _drafts_of(loop: BuiltinAgentLoop) -> tuple[UsageRecordDraft, ...]:
    """读取 loop 交回的 usage 草稿（不在冻结 ABC 上，鸭子类型消费）。"""
    drafts = getattr(loop, "usage_drafts", ())
    return tuple(drafts)


def _partial_answer_of(loop: BuiltinAgentLoop) -> str | None:
    """读取取消时已累积的部分回答（不在冻结 ABC 上，鸭子类型消费）。"""
    partial = getattr(loop, "partial_answer", None)
    return partial if isinstance(partial, str) and partial else None


def _rebuild_transcript(events: list[SessionEvent]) -> list[ChatMessage]:
    """从历史事件重建归一化 transcript（镜像重放全部成对 user/assistant 文本）。"""
    transcript: list[ChatMessage] = []
    for event in events:
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
    return transcript
