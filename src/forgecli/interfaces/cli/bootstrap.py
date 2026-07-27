"""组合根：渲染 banner、解析目录信任，命中后装配并运行交互式会话。

启动顺序（ADR-0008 首启信任）：
    banner -> 解析当前目录信任 -> 命中/信任则进 REPL，拒绝/非 TTY 则提示后退出。
banner 先于信任解析渲染，保证即便因拒绝或非 TTY 直接退出，用户仍看到 banner。
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_loop.events import LoopEventBus
from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.agent_turn.cancellation import TurnCancelSource
from forgecli.application.config.config_service import ConfigService
from forgecli.application.intent_router import IntentRouter
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.thinking import ThinkingMode
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
from forgecli.application.project import (
    ProjectContext,
    ProjectService,
    WorkspaceStartup,
)
from forgecli.application.session import SessionService
from forgecli.infrastructure.config import TomlConfigStore, config_dir, config_file
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.infrastructure.project import (
    ProcessLock,
    ProjectLockedError,
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore
from forgecli.interfaces.cli.banner import render_banner
from forgecli.interfaces.cli.llm_wiring import build_llm_runtime
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import Repl
from forgecli.interfaces.cli.stream_render import StreamingTranscript
from forgecli.interfaces.cli.tty.tty import stdin_is_tty
from forgecli.interfaces.cli.tty_prompts import TtyDirectoryPicker, TtyTrustPrompter
from forgecli.interfaces.cli.wiring import build_registry


def _project_service() -> ProjectService:
    # 项目索引与项目配置都落在用户级 Forge home 下（projects/），不写入项目目录。
    projects = config_dir() / "projects"
    return ProjectService(
        TomlProjectIndexStore(projects / "index.toml"),
        TomlProjectConfigStore(projects),
    )


def _session_service(context: ProjectContext) -> SessionService:
    # 会话事件 / 快照落在用户级 Forge home 的项目目录下（ADR-0008），不写入项目目录。
    sessions = config_dir() / "projects" / context.project.project_id / "sessions"
    return SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root=context.project.primary_workspace_root,
    )


def _prompt_runtime_status(
    config_service: ConfigService,
    llm_service: LlmConfigService,
    thinking_state: ThinkingRuntimeState | None = None,
) -> str:
    """输入框下方右侧的现读状态：项目当前模型 + 该模型的 thinking。"""
    effective = config_service.effective()
    ref = effective.default_model
    if ref is None:
        return "模型 未设置 · thinking -/-"
    catalog = build_catalog(llm_service.config())
    if not catalog.has_model(ref):
        return f"模型 {ref} · thinking 未配置"
    entry = catalog.get(ref)
    if thinking_state is not None:
        entry = thinking_state.apply(ref, entry)
    if entry.thinking_mode is ThinkingMode.OFF:
        return f"模型 {ref} · thinking off"
    effort = entry.effective_thinking_effort
    effort_text = effort.value if effort is not None else "默认"
    return f"模型 {ref} · thinking {entry.thinking_mode.value}/{effort_text}"


def run() -> None:
    """裸 forge 的产品入口。"""
    console = Console()
    render_banner(console=console)

    service = _project_service()
    startup = WorkspaceStartup(service, TtyTrustPrompter(console))
    result = startup.resolve(Path.cwd(), interactive=stdin_is_tty())
    if result.project is None:
        if result.reason == "declined":
            console.print("已取消：未信任当前目录，不创建任何配置。")
        else:  # no_tty
            console.print("[yellow]需要在终端(TTY)中确认是否信任当前目录。[/]")
        return

    context = ProjectContext(result.project)

    # 项目级排他：同一项目同一时刻只允许一个 forge 进程操作。锁文件落在用户级
    # Forge home 的项目目录下（不写进仓库），用 OS 咨询锁，进程崩溃由 OS 自动释放。
    lock = ProcessLock(
        config_dir() / "projects" / context.project.project_id / "forge.lock"
    )
    try:
        lock.acquire()
    except ProjectLockedError as exc:
        console.print(f"[yellow]{exc.message}[/]")
        return

    try:
        session = _session_service(context)
        # LLM 网关运行时（ADR-0011）：与 build_registry 共享同一批配置 service。
        # chat turn 由 AgentTurnService 驱动 BuiltinAgentLoop（ADR-0010）：模型
        # 调用经统一网关流式返回，增量进 REPL Live 区；usage 草稿随回复交回、
        # 由 AgentTurnService 落盘；Ctrl-C 经 TurnCancelSource 协作取消在途调用。
        project_home = config_dir() / "projects" / context.project.project_id
        forge_toml = project_home / "forge.toml"
        config_service = ConfigService(
            TomlConfigStore(config_file("config.toml")),
            TomlConfigStore(forge_toml),
        )
        llm_service = LlmConfigService(TomlLlmConfigStore(config_file("llm.toml")))
        thinking_state = ThinkingRuntimeState()
        llm_runtime = build_llm_runtime(
            config_service, llm_service, forge_toml, thinking_state
        )
        stream_view = StreamingTranscript(console)
        cancel_source = TurnCancelSource()
        loop_bus = LoopEventBus()

        def _new_loop() -> BuiltinAgentLoop:
            return BuiltinAgentLoop(
                llm_runtime.gateway,
                llm_runtime.usage_meter,
                cancel_token_factory=cancel_source.current,
                on_delta=stream_view.feed,
                event_bus=loop_bus,
            )

        agent_turn = AgentTurnService(session, loop_factory=_new_loop)
        output = RichOutput(console=console)
        presenter = RichMenuPresenter(console=console)
        picker = TtyDirectoryPicker(console=console)
        registry = build_registry(
            session_service=session,
            context=context,
            project_service=service,
            presenter=presenter,
            picker=picker,
            output=output,
            agent_turn=agent_turn,
            config_service=config_service,
            llm_service=llm_service,
            overrides_service=llm_runtime.overrides_service,
            thinking_state=thinking_state,
        )
        router = IntentRouter(registry=registry)
        Repl(
            console=console,
            router=router,
            registry=registry,
            output=output,
            session=session,
            agent_turn=agent_turn,
            stream_view=stream_view,
            cancel_source=cancel_source,
            prompt_status=lambda: _prompt_runtime_status(
                config_service, llm_service, thinking_state
            ),
        ).run()
    finally:
        # 正常退出 / 异常 / Ctrl-C 都释放（flock 在 kill -9 时也由 OS 释放）。
        lock.release()
