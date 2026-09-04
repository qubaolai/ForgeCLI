"""一个激活项目的组合根: Web 控制面与终端入口共用同一份装配 (ADR-0045)。

住在 ``interfaces/runtime`` 而不是任何一个入口包下, 是因为两条入口都要它: 让
``interfaces/tui`` 去 import ``interfaces/web`` 的话, 终端会话就跟着 FastAPI、
sse-starlette 与静态资源一起装配, 而它一个都用不上。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
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
from forgecli.application.planning.plan_review import (
    PlanReviewChoice,
    PlanReviewOutcome,
    PlanReviewService,
)
from forgecli.application.project.project_service import ProjectService
from forgecli.application.prompt.system_prompt_builder import SystemPromptBuilder
from forgecli.application.security.workspace_grants import GrantAccess
from forgecli.application.session.resume_service import ResumeService
from forgecli.application.session.session_service import SessionService
from forgecli.domain.conversation.turn import AssistantResponse, TurnPause, TurnStatus
from forgecli.domain.human_prompt import HumanPrompt, PromptKind
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
from forgecli.interfaces.runtime.human_prompt import BlockingHumanPromptBroker
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

    def __init__(self, project: ProjectConfig,) -> None:
        self.project = project
        # 非空表示这一轮用按脚本回话的假网关 (--mock-llm). 留着是因为 reload_llm 要用:
        # 用户在假模型下敲 /config 改点东西, 不该把它悄悄换回真网关.
        project_home = config_dir() / "projects" / project.project_id
        sessions_dir = project_home / "sessions"
        self.lock = ProcessLock(project_home / "forge.lock")
        self.lock.acquire()
        try:
            self.session = SessionService(
                JsonlEventStore(sessions_dir),
                JsonStateStore(sessions_dir),
                workspace_root=project.primary_workspace_root,
            )
            self.resume_service = ResumeService(
                FsSessionCatalog(sessions_dir),
                JsonStateStore(sessions_dir),
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
            self.cancel_source = TurnCancelSource()
            self.event_bus = AgentRunEventBus()
            self.events = RunEventHub()
            self.event_bus.subscribe(self.events)
            # 处理过程落盘 (展示用, 不是恢复真相源). 内存缓冲只有 2048 条且随进程消失,
            # 而"回看上周那一轮到底做了什么"要的正是跨进程的那一份.
            self.runs = JsonlRunStore(
                sessions_dir, lambda: self.session.current().session_id
            )
            self.event_bus.subscribe(self.runs)
            self.prompts = BlockingHumanPromptBroker()
            self.tools = self._build_tool_stack()
            self._run_lock = threading.Lock()
            self._run: TurnRun | None = None
            self._thread: threading.Thread | None = None
            self.session.start()
            self._agent_turn = self._build_agent_turn()
        except BaseException:
            self.lock.release()
            raise

    def _build_tool_stack(self) -> ToolStack:
        """主根原生可写；持久化的额外根在进程重启后保守恢复为只读。"""
        tools = build_tool_stack(
            workspace_roots=(self.project.primary_workspace_root,),
            workspace_id=self.project.project_id,
            session=self.session,
            gateway=self.llm.gateway,
            run_bus=self.event_bus,
            prompts=self.prompts,
            environment_inheritance=self.config.effective().environment_inheritance,
        )
        granted_at = now_iso()
        for root in self.project.workspace_roots[1:]:
            tools.grants.grant(root, GrantAccess.READ, granted_at=granted_at)
        return tools

    def reload_llm(self) -> None:
        """应用有效的模型/网关配置，同时保留会话与目录授权。"""
        if self.busy:
            raise RuntimeError("turn 运行期间不能重载模型配置")
        grants = self.tools.grants.grants
        self.llm = build_llm_runtime(
            self.config,
            self.llm_config,
            self.forge_json,
            self.thinking,
        )
        self.tools = self._build_tool_stack()
        for grant in grants:
            self.tools.grants.grant(
                grant.path, grant.access, granted_at=grant.granted_at
            )
        self._agent_turn = self._build_agent_turn()

    def _build_agent_turn(self) -> AgentTurnService:
        # 计量器与循环共用同一个 (ADR-0037): 各建一个迟早会出现两套单价.
        #
        # 不再需要 ArtifactStore: 归档只在工具产出那一刻发生 (ADR-0041 决策 6), 窗口维护
        # 这一层不再回头去问"这份内容还取不取得回来".
        window_manager = WindowManager(
            max_inline_bytes=self.tools.max_inline_bytes,
            gateway=self.llm.gateway,
            meter=self.llm.usage_meter,
        )

        def new_loop() -> BuiltinAgentLoop:
            return BuiltinAgentLoop(
                self.llm.gateway,
                self.llm.usage_meter,
                cancel_token_factory=self.cancel_source.current,
                event_bus=self.event_bus,
                context=window_manager,
                workspace_snapshot_provider=OsWorkspaceSnapshotProvider(
                    lambda: self.tools.context_factory().workspace_roots
                ),
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
            context=window_manager,
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
            self._thread = threading.Thread(
                target=self._execute_turn,
                args=(run.run_id, message, origin),
                name=f"forge-{run.run_id}",
                daemon=True,
            )
            self._thread.start()
            return run

    def _execute_turn(self, run_id: str, text: str, origin: InputOrigin) -> None:
        self.cancel_source.issue()
        # 本轮的提问额度归零 (ADR-0043 决策 9). 与上一句同一个位置: 这里已经是"按轮
        # 起始"该做的事所在的地方, 分开写就多了一个会忘记跟上的调用点.
        self.prompts.begin_turn()
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
        with self._run_lock:
            running = self._run is not None and self._run.status == "running"
        if running:
            _log.info("turn.cancel_requested", project_id=self.project.project_id)
            token = self.cancel_source.current()
            if token is not None:
                token.cancel()
            # 提示通道是无超时阻塞的: 不放开它, "停止"按不动一个正在等人回话的 turn.
            # 审批与 ask_user 在同一条队列上, 所以这一句同时放开两者 (ADR-0043 决策 10).
            self.prompts.release_pending("用户停止了这一轮, 未收到回答")
        return running

    def new_session(self) -> SessionSnapshot:
        if self.busy:
            raise RuntimeError("turn 运行期间不能切换会话")
        snapshot = self.session.start()
        self._agent_turn = self._build_agent_turn()
        with self._run_lock:
            self._run = None
        return snapshot

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

    def resolve_prompt(self, prompt_id: str, choice: str = "", text: str = "") -> bool:
        """记下一个人对一条待答提示的作答. 两个界面共用这一个入口.

        审批与 ask_user 走同一条队列 (ADR-0043 决策 3), 所以这里也不按工具名分派 —— 按
        ``kind`` 决定要不要多记一条会话事件.

        **回答的正文要进 events.jsonl** (决策 12): TOOL_COMPLETED 的 payload 来自
        ``ToolResult.to_audit_payload()``, 那里只有机制事实, 不记就是永久丢失 —— 而这段
        字是人写的, 不可复现.
        """
        prompt = self.prompts.find(prompt_id)
        if not self.prompts.resolve(prompt_id, choice, text):
            return False
        if prompt is not None and prompt.kind is PromptKind.QUESTION:
            self.session.record_tool_event(
                EventType.USER_QUESTION_ANSWERED,
                {
                    "prompt_id": prompt_id,
                    "question": prompt.title,
                    "choice": choice,
                    "answer": text or _label_of(prompt, choice),
                },
            )
        return True

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

    def __init__(
        self, projects: ProjectService
    ) -> None:
        self.projects = projects
        # 假网关的脚本路径 (--mock-llm). 由进程入口一路传下来而不是各自现读: 一次启动
        # 里所有项目运行时要么都是假的, 要么都是真的.
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
