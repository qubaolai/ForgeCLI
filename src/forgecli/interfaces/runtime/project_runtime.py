"""一个激活项目的组合根: Web 控制面与终端入口共用同一份装配 (ADR-0045)。

住在 ``interfaces/runtime`` 而不是任何一个入口包下, 是因为两条入口都要它: 让
``interfaces/tui`` 去 import ``interfaces/web`` 的话, 终端会话就跟着 FastAPI、
sse-starlette 与静态资源一起装配, 而它一个都用不上。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_loop.rules import builtin_rules
from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.agent_turn.cancellation import TurnCancelSource
from forgecli.application.config.config_service import ConfigService
from forgecli.application.context.runtime_facts import RuntimeFacts
from forgecli.application.context.window_manager import WindowManager
from forgecli.application.llm.availability import EnvProviderAvailability
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
from forgecli.application.llm.transport_policy import ModelTransportPolicy
from forgecli.application.planning.plan_review import (
    PlanReviewChoice,
    PlanReviewOutcome,
    PlanReviewService,
)
from forgecli.application.project.project_service import ProjectService
from forgecli.application.prompt.system_prompt_builder import SystemPromptBuilder
from forgecli.application.security.workspace_grants import GrantAccess
from forgecli.application.session.deletion_service import (
    DeletedSession,
    SessionDeletionService,
)
from forgecli.application.session.resume_service import ResumeService
from forgecli.application.session.session_catalog import Tombstone
from forgecli.application.session.session_service import SessionService
from forgecli.domain.agent.run_events import AgentRunEventKind, HumanPromptPayload
from forgecli.domain.conversation.turn import (
    AssistantResponse,
    TurnIdentity,
    TurnPause,
    TurnStatus,
)
from forgecli.domain.human_interaction.prompt import (
    HumanPrompt,
    PromptAnswer,
    PromptKind,
)
from forgecli.domain.intents import InputOrigin, SessionMode
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.thinking import ThinkingEffortName, ThinkingMode
from forgecli.domain.planning import PlanIndex
from forgecli.domain.recovery.checkpoint import RecoveryCheckpoint
from forgecli.domain.session.events import EventType, SessionEvent
from forgecli.domain.session.snapshot import SessionSnapshot
from forgecli.domain.workspace.project import ProjectConfig
from forgecli.infrastructure.config import JsonConfigStore, config_dir, config_file
from forgecli.infrastructure.llm import JsonLlmConfigStore
from forgecli.infrastructure.project import (
    JsonProjectConfigStore,
    JsonProjectIndexStore,
    ProcessLock,
)
from forgecli.infrastructure.prompt import FsProjectInstructionReader
from forgecli.infrastructure.session.fs_session_catalog import FsSessionCatalog
from forgecli.infrastructure.session.json_state_store import JsonStateStore
from forgecli.infrastructure.session.jsonl_event_store import JsonlEventStore
from forgecli.infrastructure.session.jsonl_run_store import JsonlRunStore
from forgecli.infrastructure.workspace.snapshot_provider import (
    OsWorkspaceSnapshotProvider,
)
from forgecli.interfaces.runtime.event_hub import RunEventHub
from forgecli.interfaces.runtime.human_interaction.broker import (
    BlockingHumanPromptBroker,
)
from forgecli.interfaces.runtime.llm_wiring import LlmRuntime, build_llm_runtime
from forgecli.interfaces.runtime.tool_wiring import ToolStack, build_tool_stack
from forgecli.shared.errors import SessionStateError
from forgecli.shared.observability.log import get_log
from forgecli.shared.serialization import to_jsonable
from forgecli.shared.utils import now_iso

_log = get_log(__name__)


@dataclass(frozen=True)
class TurnRun:
    run_id: str
    status: str
    response: AssistantResponse | None = None
    error: str = ""


class ProjectRuntime:
    """一个激活项目的长生命周期对象；同一时间只运行一个 turn。"""

    def __init__(
        self,
        project: ProjectConfig,
    ) -> None:
        self.project = project
        project_home = config_dir() / "projects" / project.project_id
        sessions_dir = project_home / "sessions"
        self.lock = ProcessLock(project_home / "forge.lock")
        self.lock.acquire()
        try:
            # 目录枚举与快照读取留一份引用: 恢复与删除都要用, 而它们无状态。
            self._session_catalog = FsSessionCatalog(sessions_dir)
            self._session_states = JsonStateStore(sessions_dir)
            self.resume_service = ResumeService(
                self._session_catalog,
                self._session_states,
                JsonlEventStore(sessions_dir),
            )
            self.forge_json = project_home / "forge.json"
            self.config = ConfigService(
                JsonConfigStore(config_file("config.json")),
                JsonConfigStore(self.forge_json),
            )
            self.llm_config = LlmConfigService(
                JsonLlmConfigStore(config_file("llm.json"))
            )
            self.thinking = ThinkingRuntimeState()
            self.availability = EnvProviderAvailability()
            self.llm: LlmRuntime = build_llm_runtime(
                self.config,
                self.llm_config,
                self.forge_json,
                self.thinking,
            )
            self.session = SessionService(
                JsonlEventStore(sessions_dir),
                JsonStateStore(sessions_dir),
                workspace_root=project.primary_workspace_root,
                gateway=self.llm.gateway,
            )
            self.cancel_source = TurnCancelSource()
            self.event_bus = AgentRunEventBus()
            self.events = RunEventHub()
            self.event_bus.subscribe(self.events)
            # 处理过程落盘 (展示用, 不是恢复真相源). 内存缓冲只有 2048 条且随进程消失,
            # 而"回看上周那一轮到底做了什么"要的正是跨进程的那一份.
            self.runs = JsonlRunStore(sessions_dir)
            self.event_bus.subscribe(self.runs)
            self.prompts = BlockingHumanPromptBroker(self._prompt_changed)
            self.tools = self._build_tool_stack(self.llm)
            self._run_lock = threading.Lock()
            self._run: TurnRun | None = None
            self._thread: threading.Thread | None = None
            self.session.start()
            self._agent_turn = self._build_agent_turn()
            self._sweep_tombstones()
        except BaseException:
            self.lock.release()
            raise

    def _build_tool_stack(self, llm: LlmRuntime) -> ToolStack:
        """主根原生可写；持久化的额外根在进程重启后保守恢复为只读。

        ``llm`` 显式传入而不是读 ``self.llm``: 重载时要先把新的一整套建出来, 建成了
        才提交 —— 读 ``self.llm`` 就必须先把它换掉, 而中途失败就再也回不去了。
        """
        tools = build_tool_stack(
            workspace_roots=(self.project.primary_workspace_root,),
            workspace_id=self.project.project_id,
            session=self.session,
            gateway=llm.gateway,
            run_bus=self.event_bus,
            prompts=self.prompts,
            environment_inheritance=self.config.effective().environment_inheritance,
        )
        granted_at = now_iso()
        for root in self.project.workspace_roots[1:]:
            tools.grants.grant(root, GrantAccess.READ, granted_at=granted_at)
        return tools

    def reload_llm(self) -> None:
        """应用有效的模型/网关配置，同时保留会话、窗口与目录授权 (ADR-0048 决策 1)。

        两条约束: **整套建成了才提交**, 中途失败时旧运行配置原样可用, 不留下半新半旧
        的组合; **会话服务只重配置, 不重建**, 换模型的人还在同一个会话里, 轮次编号与
        窗口内容都不该因为改了一项配置而消失。

        新模型窗口更小时不在这里处理: 那是 ``WindowManager`` 每轮开头按预算压缩的事
        (ADR-0041), 清空会话既解决不了问题, 也把用户的上文一起丢了。
        """
        if self.busy:
            raise RuntimeError("turn 运行期间不能重载模型配置")
        grants = self.tools.grants.grants
        llm = build_llm_runtime(
            self.config,
            self.llm_config,
            self.forge_json,
            self.thinking,
        )
        tools = self._build_tool_stack(llm)
        for grant in grants:
            tools.grants.grant(grant.path, grant.access, granted_at=grant.granted_at)
        # 到这里两件都建成了, 一起换上. 之前的顺序是先换 self.llm 再建工具栈, 于是
        # 工具栈建失败时进程停在"新网关 + 旧工具栈"上, 而没有任何东西会说它坏了。
        self.llm, self.tools = llm, tools
        self._window_manager = self._build_window_manager()
        self._agent_turn.reconfigure(
            context_budget=self.llm.context_budget,
            context=self._window_manager,
            memory=self.tools.memory,
            planning=self.tools.planning,
            tools=self.tools.dispatcher,
        )

    def _build_window_manager(self) -> WindowManager:
        # 计量器与循环共用同一个 (ADR-0037): 各建一个迟早会出现两套单价.
        #
        # 不再需要 ArtifactStore: 归档只在工具产出那一刻发生 (ADR-0041 决策 6), 窗口维护
        # 这一层不再回头去问"这份内容还取不取得回来".
        return WindowManager(
            max_inline_bytes=self.tools.max_inline_bytes,
            gateway=self.llm.gateway,
            meter=self.llm.usage_meter,
        )

    def _build_agent_turn(self) -> AgentTurnService:
        self._window_manager = self._build_window_manager()

        def new_loop() -> BuiltinAgentLoop:
            return BuiltinAgentLoop(
                self.llm.gateway,
                self.llm.usage_meter,
                # 每轮现建一份规则表 (ADR-0049): 好几条规则带着本轮的计数器. 读字段
                # 而不是闭包住一个实例: /compact 走服务那一份, 自动压缩走循环这一份,
                # 换模型之后两边必须还是同一个 (ADR-0048 决策 1).
                rules=builtin_rules(
                    context=self._window_manager,
                    workspace_provider=OsWorkspaceSnapshotProvider(
                        lambda: self.tools.context_factory().workspace_roots
                    ),
                ),
                cancel_token_factory=self.cancel_source.current,
                model_transport_policy=ModelTransportPolicy(),
                event_bus=self.event_bus,
            )

        def runtime_facts() -> RuntimeFacts:
            execution = self.tools.context_factory()
            return RuntimeFacts.from_profile(
                self.tools.profile,
                working_directory=execution.cwd,
                workspace_roots=execution.workspace_roots,
                git_repository=(Path(execution.cwd) / ".git").exists(),
            )

        return AgentTurnService(
            self.session,
            loop_factory=new_loop,
            prompt_builder=SystemPromptBuilder(),
            runtime_facts=runtime_facts,
            instructions=FsProjectInstructionReader(),
            context_budget=self.llm.context_budget,
            context=self._window_manager,
            memory=self.tools.memory,
            planning=self.tools.planning,
            run_bus=self.event_bus,
            tools=self.tools.dispatcher,
        )

    @property
    def busy(self) -> bool:
        with self._run_lock:
            return self._run is not None and self._run.status == "running"

    def current_run(self) -> TurnRun | None:
        with self._run_lock:
            return self._run

    def current_turn_identity(self) -> TurnIdentity | None:
        """正在跑的那一轮在会话里的身份 (ADR-0048 决策 2)。

        与 ``TurnRun.run_id`` 是两件事, 不能互相代用: ``run_id`` 标识"这次后台执行",
        由组合根生成, 每次 ``start_turn`` 一个新的; 这一对标识"会话里的第几轮", 由会话
        服务生成。把它们对应关系公开出来, 调用方就不必自己拼一个。
        """
        return self._agent_turn.current_turn()

    def start_turn(self, text: str, *, origin: InputOrigin) -> TurnRun:
        """起一轮。``origin`` 没有默认值: 两条入口各自说明这句话是谁给的。"""
        message = text.strip()
        if not message:
            raise ValueError("消息不能为空")
        with self._run_lock:
            if self._run is not None and self._run.status == "running":
                raise RuntimeError("当前项目已有正在运行的 turn")
            run = TurnRun(run_id=f"run_{uuid.uuid4().hex[:12]}", status="running")
            self._run = run
            # 轮次生命周期在起线程之前就位 (ADR-0048 决策 3): 从这里返回到后台线程真正
            # 跑起来之间有一段真空, 而"停止"随时可能落在那一段里. 以前这两句在后台线程
            # 开头, 于是那一段里的取消要么取消掉上一轮的 token, 要么什么都没取消 ——
            # 两种都会返回成功。
            self.cancel_source.issue()
            # 本轮的提问额度归零 (ADR-0043 决策 9), 同时解除上一轮的取消闩。
            self.prompts.begin_turn()
            self._thread = threading.Thread(
                target=self._execute_turn,
                args=(run.run_id, message, origin),
                name=f"forge-{run.run_id}",
                daemon=True,
            )
            self._thread.start()
            return run

    def _execute_turn(self, run_id: str, text: str, origin: InputOrigin) -> None:
        _log.info(
            "turn.started",
            run_id=run_id,
            project_id=self.project.project_id,
            origin=origin.value,
            chars=len(text),
        )
        try:
            response = self._agent_turn.handle_user_message(text, origin=origin)
            # 终态照 response 说的报, 不一律当成 completed: 驱动抛异常时 service 会
            # 把它隔离成一个 FAILED 的轮次并如实写进会话事件, 而这里报 completed 就是
            # 在日志与 `/turns/current` 上把那次失败说成成功。
            if response.pause is TurnPause.PLAN_REVIEW:
                status = "waiting_plan_review"
            elif response.status is TurnStatus.FAILED:
                status = "failed"
            else:
                status = "completed"
            completed = TurnRun(run_id=run_id, status=status, response=response)
            _log.info("turn.finished", run_id=run_id, status=status)
        except Exception as exc:  # noqa: BLE001 - 后台边界必须转成可查询状态
            # 这里是后台线程的最外层: 异常被压成一个字符串状态之后, traceback 就再也
            # 拿不回来了 —— 而页面上只会显示一行错误文案.
            _log.exception(
                "turn.failed",
                run_id=run_id,
                error=type(exc).__name__,
                message=str(exc),
            )
            completed = TurnRun(
                run_id=run_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            self.cancel_source.clear()
        with self._run_lock:
            self._run = completed

    def cancel(self) -> bool:
        """停这一轮。判定与两处取消都在 ``_run_lock`` 内 (ADR-0048 决策 3)。

        持锁到底是因为 ``start_turn`` 也在这把锁里发 token: 放开锁再取消的话, 判定与
        取消之间可以插进另一轮的开始, 于是新起的这一轮被上一次"停止"顺手取消掉。
        """
        with self._run_lock:
            running = self._run is not None and self._run.status == "running"
            if not running:
                return False
            _log.info("turn.cancel_requested", project_id=self.project.project_id)
            token = self.cancel_source.current()
            if token is not None:
                token.cancel()
            # 顺序有意义: 先置 token 再闩提示通道。ask_user 拿到"没有回答"之后要靠
            # token 分辨这是取消还是没人可答, 反过来就会把取消报成"无人回答"。
            #
            # 提示通道是无超时阻塞的: 不闩它, "停止"按不动一个正在等人回话的 turn。
            # 审批与 ask_user 同队列, 所以这一句同时管住两者 (ADR-0043 决策 10)。
            self.prompts.cancel_turn("用户停止了这一轮, 未收到回答")
            return True

    def new_session(self) -> SessionSnapshot:
        if self.busy:
            raise RuntimeError("turn 运行期间不能切换会话")
        snapshot = self.session.start()
        self._agent_turn = self._build_agent_turn()
        with self._run_lock:
            self._run = None
        return snapshot

    def delete_session(self, session_id: str) -> tuple[DeletedSession, SessionSnapshot]:
        """删掉一个会话, 返回 (删了什么, 删完之后的当前会话)。

        删的正好是当前会话时, 紧接着开一个新的: 运行时必须始终有一个当前会话 ——
        让它悬空的话, 下一条消息会往一个已经不在磁盘上的会话里落盘, 而那不会报错。

        **只等到会话从列表消失为止。** 真正删文件 (释放写时复制快照, 回收 blob,
        rmtree 会话目录) 交给后台 —— 那部分的耗时随项目大小走, 没有上界, 留在请求里
        迟早超时。
        """
        if self.busy:
            raise RuntimeError("turn 运行期间不能删除会话")
        deleting_current = session_id == self.session.current().session_id
        if deleting_current and self._session_states.read(session_id) is None:
            # 会话要到第一条可记录事件才落盘 (SessionService._ensure_persisted), 所以
            # 一个还没说过话的当前会话在磁盘上什么都没有。"删掉它"这件事仍然成立 ——
            # 换一个新的就是了, 而不是报一句"未找到会话"让人以为出了错。
            snapshot = self.session.current()
            return (
                DeletedSession(session_id=session_id, title=snapshot.title),
                self.new_session(),
            )
        service = self._deletion_service()
        report, tombstone = service.begin(session_id)
        self._purge_in_background(service, (tombstone,))
        if deleting_current:
            return report, self.new_session()
        return report, self.session.current()

    def _purge_in_background(
        self, service: SessionDeletionService, tombstones: Sequence[Tombstone]
    ) -> None:
        """后台清墓碑。

        daemon 线程且不 join: 清理没清完就退出进程不会丢东西 —— 墓碑还在, 下次启动
        的扫描接着清。反过来为它卡住退出, 换来的只是一个关不掉的窗口。
        """
        if not tombstones:
            return
        threading.Thread(
            target=lambda: [service.purge(item) for item in tombstones],
            name=f"forge-purge-{self.project.project_id}",
            daemon=True,
        ).start()

    def _sweep_tombstones(self) -> None:
        """启动时把上一次没清完的墓碑接着清掉。"""
        service = self._deletion_service()
        self._purge_in_background(service, service.pending())

    def _deletion_service(self) -> SessionDeletionService:
        """现建而不是存一份: 恢复点协作件随工具栈一起重建 (见 ``reload_llm``),
        存下来的那份会在换过模型之后指向一个没人再用的实例。"""
        return SessionDeletionService(
            self._session_catalog,
            self._session_states,
            workspace_id=self.tools.workspace_id,
            recovery=self.tools.mutations,
        )

    def resume(self, session_id: str) -> SessionSnapshot:
        if self.busy:
            raise RuntimeError("turn 运行期间不能恢复其他会话")
        snapshot, history = self.resume_service.load_full(session_id)
        resumed = self.session.resume(snapshot, history)
        self._agent_turn = self._build_agent_turn()
        self._agent_turn.resume(history)
        with self._run_lock:
            self._run = None
        return resumed

    def _prompt_changed(self, prompt_id: str, kind: str) -> None:
        """把"有人要回话了 / 回完了"发成运行事件。

        身份问会话服务要, 不读日志上下文 (ADR-0048 决策 2)。旧写法从
        ``current_run_context()`` 反推 turn_id, 那是排查用的线程局部量: 它今天恰好
        对得上, 只因为通道是在跑这一轮的那个线程里阻塞的 —— 换成从别的线程放开提示,
        事件就会带着空归属或者上一轮的归属发出去, 而没有任何东西会报错。
        """
        identity = self._agent_turn.current_turn()
        if identity is None:
            return
        self.event_bus.publish(
            AgentRunEventKind(kind),
            session_id=identity.session_id,
            turn_id=identity.turn_id,
            payload=HumanPromptPayload(prompt_id=prompt_id),
        )

    def resolve_prompt(
        self,
        prompt_id: str,
        choice: str = "",
        text: str = "",
        *,
        selected_values: tuple[str, ...] = (),
        skipped: bool = False,
    ) -> bool:
        """记下一个人对一条待答提示的作答. 两个界面共用这一个入口.

        审批与 ask_user 走同一条队列 (ADR-0043 决策 3), 所以这里也不按工具名分派 —— 按
        ``kind`` 决定要不要多记一条会话事件.

        **回答的正文要进 events.jsonl** (决策 12): TOOL_COMPLETED 的 payload 来自
        ``ToolResult.to_audit_payload()``, 那里只有机制事实, 不记就是永久丢失 —— 而这段
        字是人写的, 不可复现.
        """
        return self.prompts.resolve(
            prompt_id,
            choice,
            text,
            selected_values=selected_values,
            skipped=skipped,
            before_resolve=self._record_prompt_answer,
        )

    def _record_prompt_answer(self, prompt: HumanPrompt, answer: PromptAnswer) -> None:
        # 持久化成功后才唤醒工具，避免下一条模型调用先于回答审计发生。
        if prompt.kind is not PromptKind.QUESTION:
            return
        values = answer.selected_values or ((answer.choice,) if answer.choice else ())
        self.session.record_tool_event(
            EventType.USER_QUESTION_ANSWERED,
            {
                "prompt_id": prompt.prompt_id,
                "question": prompt.title,
                "choice": answer.choice,
                "status": "skipped" if answer.skipped else "answered",
                "selected_values": list(values),
                "text": answer.text,
                "answer": "用户跳过了本题，未提供答案"
                if answer.skipped
                else "\n".join(
                    [
                        *(_label_of(prompt, value) for value in values),
                        *([answer.text] if answer.text else []),
                    ]
                ),
            },
        )

    def resolve_plan_review(
        self, choice: PlanReviewChoice, note: str = ""
    ) -> PlanReviewOutcome | None:
        """裁决当前待评审的计划. 返回 None 表示当前没有待评审的计划.

        返回 outcome 而不是一个 bool: 终端要把 ``message`` 原样打给用户, 而 Web 只判
        真假. 让终端自己再拼一句"计划已批准", 同一件事就有了两种说法.
        """
        with self._run_lock:
            run = self._run
        if run is None or run.status != "waiting_plan_review":
            return None
        active = self.tools.planning.load()
        if active.plan is None:
            return None
        outcome = PlanReviewService(self.tools.planning).decide(
            choice, active.plan, mode=self.session.current().mode, note=note
        )
        self.session.record_tool_event(
            EventType.PLAN_REVIEWED,
            {
                "plan_id": active.plan.plan_id,
                "revision": active.plan.revision,
                "decision": choice.value,
                "upgraded_mode": (
                    "" if outcome.upgraded_mode is None else outcome.upgraded_mode.value
                ),
            },
        )
        if outcome.upgraded_mode is not None:
            self.session.set_mode(outcome.upgraded_mode)
        with self._run_lock:
            self._run = TurnRun(
                run_id=run.run_id,
                status="completed",
                response=run.response,
            )
        if outcome.follow_up:
            self.start_turn(outcome.follow_up, origin=InputOrigin.PROGRAM)
        return outcome

    def set_mode(self, mode: SessionMode) -> SessionSnapshot:
        if self.busy:
            raise RuntimeError("turn 运行期间不能切换模式")
        return self.session.set_mode(mode)

    def grant_workspace(self, path: Path, *, write: bool) -> object:
        access = GrantAccess.WRITE if write else GrantAccess.READ
        grant = self.tools.grants.grant(str(path), access, granted_at=now_iso())
        self.session.record_tool_event(
            EventType.DIR_GRANT_CHANGED,
            {"path": str(path), "access": access.value, "action": "grant"},
        )
        return grant

    def revoke_workspace(self, path: Path) -> bool:
        revoked = self.tools.grants.revoke(str(path))
        if revoked:
            self.session.record_tool_event(
                EventType.DIR_GRANT_CHANGED,
                {"path": str(path), "action": "revoke"},
            )
        return revoked

    # ---- 模型选择 ----

    def current_model(self) -> ModelRef | None:
        """当前默认模型 (PUT /api/v1/models/current)。未配置时为 None。"""
        return self.config.effective().default_model

    def set_current_model(self, provider: str, model: str) -> None:
        """两个配置键一起改: 只写一半会得到一个指向不存在模型的组合。"""
        if self.llm_config.config().model(provider, model) is None:
            raise ValueError(f"模型未在 LLM 配置中声明: {provider}:{model}")
        self.config.set("model.provider", provider)
        self.config.set("model.name", model)
        self.reload_llm()

    def model_overrides(self) -> dict[str, str]:
        """按用途覆盖 (按 origin 覆盖当前模型)。未设置的用途不出现在结果里。"""
        overrides = self.llm.overrides_service.overrides()
        return {origin.value: str(ref) for origin, ref in overrides.items()}

    def set_model_override(self, origin: str, provider: str, model: str) -> None:
        self.llm.overrides_service.set_override(
            RequestOrigin(origin), ModelRef(provider=provider, model=model)
        )
        self.reload_llm()

    def clear_model_override(self, origin: str) -> None:
        self.llm.overrides_service.clear_override(RequestOrigin(origin))
        self.reload_llm()

    # ---- Thinking 运行时覆盖 ----

    def thinking_view(self) -> dict[str, object]:
        """当前模型的有效 thinking 设置与可选强度 (PUT /api/v1/models/thinking)。"""
        ref = self.current_model()
        if ref is None:
            return {"model": "", "configured": False}
        entry = self._catalog_entry(ref)
        if entry is None:
            return {"model": str(ref), "configured": False}
        applied = self.thinking.apply(ref, entry)
        effort = applied.effective_thinking_effort
        return {
            "model": str(ref),
            "configured": True,
            "mode": applied.thinking_mode.value,
            "effort": "" if effort is None else effort.value,
            "supported_efforts": [
                item.value for item in applied.thinking_capabilities.efforts
            ],
        }

    def update_thinking(self, mode: str, effort: str) -> bool:
        """只改本进程的当前模型 thinking，不落盘 —— 与 /thinking 语义一致。"""
        ref = self.current_model()
        if ref is None:
            raise ValueError("当前未设置模型；请先选择一个模型。")
        entry = self._catalog_entry(ref)
        if entry is None:
            raise ValueError(f"模型不存在: {ref}")
        return self.thinking.update(
            ref,
            entry,
            mode=ThinkingMode(mode) if mode else None,
            effort=ThinkingEffortName(effort) if effort else None,
        )

    def _catalog_entry(self, ref: ModelRef) -> ModelCatalogEntry | None:
        catalog = build_catalog(self.llm_config.config())
        return catalog.get(ref) if catalog.has_model(ref) else None

    # ---- 计划目录 ----

    def plan_index(self) -> PlanIndex:
        return self.tools.planning.load_index()

    def activate_plan(self, plan_id: str) -> bool:
        return self.tools.planning.set_active_plan(plan_id)

    # ---- 状态与恢复 ----

    def status(self) -> dict[str, object]:
        snapshot = self.session.current()
        model = self.current_model()
        return {
            "session_id": snapshot.session_id,
            "mode": snapshot.mode.value,
            "last_event_id": snapshot.last_event_id or "",
            "workspace_roots": list(self.project.workspace_roots),
            "model": "" if model is None else str(model),
            "busy": self.busy,
        }

    def recovery_status(self) -> dict[str, object]:
        """恢复层状态 (GET /api/v1/recovery): 恢复点总数与未收尾事务。"""
        recovery = self.tools.recovery
        pending = recovery.crash_recovery_candidates(self.tools.workspace_id)
        return {
            "checkpoint_count": len(self.list_checkpoints()),
            "pending": [to_jsonable(item) for item in pending],
        }

    def list_checkpoints(self) -> tuple[RecoveryCheckpoint, ...]:
        return self.tools.recovery.list_checkpoints(self.tools.workspace_id)

    def checkpoint(self, checkpoint_id: str) -> RecoveryCheckpoint | None:
        return next(
            (
                item
                for item in self.list_checkpoints()
                if checkpoint_id in (item.checkpoint_id, item.tool_invocation_id)
            ),
            None,
        )

    def list_sessions(self) -> list[SessionSnapshot]:
        return self.resume_service.list_sessions(limit=100)

    def transcript(self, session_id: str) -> tuple[SessionEvent, ...]:
        try:
            _, events = self.resume_service.load_full(session_id)
        except SessionStateError:
            if session_id == self.session.current().session_id:
                return ()
            raise
        return tuple(
            item
            for item in events
            if item.type in (EventType.USER_MESSAGE, EventType.ASSISTANT_MESSAGE)
        )

    def close(self) -> None:
        self.cancel()
        self.prompts.close()
        self.events.close()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self.lock.release()


class ProjectRuntimeRegistry:
    """本地服务只激活一个项目；项目中心本身仍可列出全部项目。"""

    def __init__(self, projects: ProjectService) -> None:
        self.projects = projects
        self._lock = threading.Lock()
        self._active: ProjectRuntime | None = None

    @property
    def active(self) -> ProjectRuntime | None:
        with self._lock:
            return self._active

    def activate(self, project_id: str) -> ProjectRuntime:
        project = self.projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        with self._lock:
            current = self._active
            if current is not None and current.project.project_id == project_id:
                return current
            if current is not None and current.busy:
                raise RuntimeError("当前项目仍有 turn 在运行")
            if current is not None:
                self._active = None
                current.close()
            activated = ProjectRuntime(project)
            self._active = activated
            return activated

    def close(self) -> None:
        with self._lock:
            active, self._active = self._active, None
        if active is not None:
            active.close()


def build_project_service() -> ProjectService:
    projects = config_dir() / "projects"
    return ProjectService(
        JsonProjectIndexStore(projects / "index.json"),
        JsonProjectConfigStore(projects),
    )


def _label_of(prompt: HumanPrompt, choice: str) -> str:
    """点了选项时记那一项的 label. value 是给机器的, 事件流是给人读的."""
    for item in prompt.choices:
        if item.value == choice:
            return item.label
    return choice
