"""项目级本地 Web Runtime：复用现有 Agent、工具、安全与存储语义。"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.agent_turn.cancellation import TurnCancelSource
from forgecli.application.config.config_service import ConfigService
from forgecli.application.llm.availability import EnvProviderAvailability
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
from forgecli.application.planning import PlanReviewChoice, PlanReviewService
from forgecli.application.project import ProjectContext, ProjectService
from forgecli.application.prompt.runtime_facts import RuntimeFacts
from forgecli.application.prompt.system_prompt_builder import SystemPromptBuilder
from forgecli.application.security.workspace_grants import GrantAccess
from forgecli.application.session import ResumeService, SessionService
from forgecli.domain.conversation.turn import AssistantResponse, TurnPause
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
from forgecli.infrastructure.project import JsonProjectConfigStore, ProcessLock
from forgecli.infrastructure.prompt import FsProjectInstructionReader
from forgecli.infrastructure.session import (
    FsSessionCatalog,
    JsonlEventStore,
    JsonStateStore,
)
from forgecli.interfaces.runtime.llm_wiring import LlmRuntime, build_llm_runtime
from forgecli.interfaces.runtime.tool_wiring import ToolStack, build_tool_stack
from forgecli.interfaces.web.approval import WebApprovalBroker
from forgecli.interfaces.web.events import WebEventHub
from forgecli.interfaces.web.serialization import to_jsonable
from forgecli.shared.errors import SessionStateError


@dataclass(frozen=True)
class TurnRun:
    run_id: str
    status: str
    response: AssistantResponse | None = None
    error: str = ""


class ProjectRuntime:
    """一个激活项目的长生命周期对象；同一时间只运行一个 turn。"""

    def __init__(self, project: ProjectConfig) -> None:
        self.project = project
        self.context = ProjectContext(project)
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
                self.config, self.llm_config, self.forge_json, self.thinking
            )
            self.cancel_source = TurnCancelSource()
            self.event_bus = AgentRunEventBus()
            self.events = WebEventHub()
            self.event_bus.subscribe(self.events)
            self.approvals = WebApprovalBroker()
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
            approval=self.approvals,
        )
        granted_at = datetime.now().astimezone().isoformat()
        for root in self.project.workspace_roots[1:]:
            tools.grants.grant(root, GrantAccess.READ, granted_at=granted_at)
        return tools

    def reload_llm(self) -> None:
        """应用有效的模型/网关配置，同时保留会话与目录授权。"""
        if self.busy:
            raise RuntimeError("turn 运行期间不能重载模型配置")
        grants = self.tools.grants.grants
        self.llm = build_llm_runtime(
            self.config, self.llm_config, self.forge_json, self.thinking
        )
        self.tools = self._build_tool_stack()
        for grant in grants:
            self.tools.grants.grant(
                grant.path, grant.access, granted_at=grant.granted_at
            )
        self._agent_turn = self._build_agent_turn()

    def _build_agent_turn(self) -> AgentTurnService:
        def new_loop() -> BuiltinAgentLoop:
            return BuiltinAgentLoop(
                self.llm.gateway,
                self.llm.usage_meter,
                cancel_token_factory=self.cancel_source.current,
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
            planning=self.tools.planning,
            run_bus=self.event_bus,
            tools=self.tools.dispatcher,
            barrier=self.tools.barrier,
        )

    @property
    def busy(self) -> bool:
        with self._run_lock:
            return self._run is not None and self._run.status == "running"

    def current_run(self) -> TurnRun | None:
        with self._run_lock:
            return self._run

    def start_turn(
        self, text: str, *, origin: InputOrigin = InputOrigin.WEB_USER
    ) -> TurnRun:
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
        try:
            response = self._agent_turn.handle_user_message(text, origin=origin)
            status = (
                "waiting_plan_review"
                if response.pause is TurnPause.PLAN_REVIEW
                else "completed"
            )
            completed = TurnRun(run_id=run_id, status=status, response=response)
        except Exception as exc:  # noqa: BLE001 - 后台边界必须转成可查询状态
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
            token = self.cancel_source.current()
            if token is not None:
                token.cancel()
            # 审批 broker 是无超时阻塞的: 不放开它, "停止"按不动一个正在等审批的 turn。
            self.approvals.release_pending("用户停止了这一轮，审批未完成")
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

    def resolve_plan_review(self, choice: PlanReviewChoice, note: str = "") -> bool:
        with self._run_lock:
            run = self._run
        if run is None or run.status != "waiting_plan_review":
            return False
        active = self.tools.planning.load()
        if active.plan is None:
            return False
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
        return True

    def set_mode(self, mode: SessionMode) -> SessionSnapshot:
        if self.busy:
            raise RuntimeError("turn 运行期间不能切换模式")
        return self.session.set_mode(mode)

    def grant_workspace(self, path: Path, *, write: bool) -> object:
        access = GrantAccess.WRITE if write else GrantAccess.READ
        grant = self.tools.grants.grant(
            str(path), access, granted_at=datetime.now().astimezone().isoformat()
        )
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
        """当前默认模型 (CLI 的 /model)。未配置时为 None。"""
        return self.config.effective().default_model

    def set_current_model(self, provider: str, model: str) -> None:
        """两个配置键一起改: 只写一半会得到一个指向不存在模型的组合。"""
        if self.llm_config.config().model(provider, model) is None:
            raise ValueError(f"模型未在 LLM 配置中声明: {provider}:{model}")
        self.config.set("model.provider", provider)
        self.config.set("model.name", model)
        self.reload_llm()

    def model_overrides(self) -> dict[str, str]:
        """按用途覆盖 (CLI 的 /config 用途模型覆盖)。未设置的用途不出现在结果里。"""
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
        """当前模型的有效 thinking 设置与可选强度 (CLI 的 /thinking)。"""
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
            "workspace_roots": list(self.context.project.workspace_roots),
            "model": "" if model is None else str(model),
            "busy": self.busy,
        }

    def recovery_status(self) -> dict[str, object]:
        """恢复层状态 (CLI 的 /recovery): 恢复点总数与未收尾事务。"""
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
        self.approvals.close()
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
    from forgecli.infrastructure.project import JsonProjectIndexStore

    return ProjectService(
        JsonProjectIndexStore(projects / "index.json"),
        JsonProjectConfigStore(projects),
    )
