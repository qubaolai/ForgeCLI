"""组合根：渲染 banner、解析目录信任，命中后装配并运行交互式会话。

启动顺序（ADR-0008 首启信任）：
    banner -> 解析当前目录信任 -> 命中/信任则进 REPL，拒绝/非 TTY 则提示后退出。
banner 先于信任解析渲染，保证即便因拒绝或非 TTY 直接退出，用户仍看到 banner。
"""

from __future__ import annotations

import os
from pathlib import Path
from types import MappingProxyType

from rich.console import Console

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.agent_turn.cancellation import TurnCancelSource
from forgecli.application.config.config_service import ConfigService
from forgecli.application.context.manager import ContextManager
from forgecli.application.intent_router import IntentRouter
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
from forgecli.application.manual_shell.provider import ManualShellContext
from forgecli.application.manual_shell.service import ManualShellService
from forgecli.application.planning.plan_review import PlanReviewService
from forgecli.application.project.project import ProjectContext
from forgecli.application.project.project_service import (
    ProjectService,
    WorkspaceStartup,
)
from forgecli.application.prompt.runtime_facts import RuntimeFacts
from forgecli.application.prompt.system_prompt_builder import SystemPromptBuilder
from forgecli.application.session.session_service import SessionService
from forgecli.domain.manual_shell.request import TerminalSize
from forgecli.domain.model.thinking import ThinkingMode
from forgecli.infrastructure.config import JsonConfigStore, config_dir, config_file
from forgecli.infrastructure.llm import JsonLlmConfigStore
from forgecli.infrastructure.manual_shell import (
    SystemShellResolver,
    build_interactive_shell_provider,
)
from forgecli.infrastructure.project import (
    JsonProjectConfigStore,
    JsonProjectIndexStore,
    ProcessLock,
    ProjectLockedError,
)
from forgecli.infrastructure.prompt import FsProjectInstructionReader
from forgecli.infrastructure.session.json_state_store import JsonStateStore
from forgecli.infrastructure.session.jsonl_event_store import JsonlEventStore
from forgecli.interfaces.cli.banner import render_banner
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.plan_review_prompt import PlanReviewPrompt
from forgecli.interfaces.cli.privilege import is_elevated  # noqa: F401  见 run()
from forgecli.interfaces.cli.repl import Repl
from forgecli.interfaces.cli.run_renderer import TerminalRunRenderer
from forgecli.interfaces.cli.shell_mode import (
    ConsoleManualShellObserver,
    ShellModeEntry,
)
from forgecli.interfaces.cli.terminal_lease import CliTerminalLease
from forgecli.interfaces.cli.tty.tty import stdin_is_tty
from forgecli.interfaces.cli.tty_approval import TtyApprovalService
from forgecli.interfaces.cli.tty_prompts import TtyDirectoryPicker, TtyTrustPrompter
from forgecli.interfaces.cli.wiring import build_registry
from forgecli.interfaces.exit_codes import ExitCode
from forgecli.interfaces.runtime.llm_wiring import build_llm_runtime
from forgecli.interfaces.runtime.logging_wiring import start_logging
from forgecli.interfaces.runtime.tool_wiring import ToolStack, build_tool_stack
from forgecli.shared import __version__
from forgecli.shared.observability.log import get_log

_log = get_log(__name__)

# 正文不出现方括号, 免得被 Rich 当成样式标记解析.
_ELEVATED_REFUSAL = (
    "[yellow]拒绝启动: 检测到当前以 root / 管理员身份运行.[/]\n"
    "请以普通用户身份重新运行 forge (不要加 sudo)."
)

_NO_TTY_REFUSAL = (
    "[yellow]拒绝启动: 当前不在终端(TTY)中运行。[/]\n"
    "Forge 是交互式会话, 输入框依赖真终端, 在管道 / CI / 重定向下无法工作。"
)


def _project_service() -> ProjectService:
    # 项目索引与项目配置都落在用户级 Forge home 下（projects/），不写入项目目录。
    projects = config_dir() / "projects"
    return ProjectService(
        JsonProjectIndexStore(projects / "index.json"),
        JsonProjectConfigStore(projects),
    )


def _session_service(context: ProjectContext) -> SessionService:
    # 会话事件 / 快照落在用户级 Forge home 的项目目录下（ADR-0008），不写入项目目录。
    sessions = config_dir() / "projects" / context.project.project_id / "sessions"
    return SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root=context.project.primary_workspace_root,
    )


def _build_shell_mode(
    console: Console, renderer: TerminalRunRenderer, tools: ToolStack
) -> ShellModeEntry:
    """装配人工 Shell (ADR-0017).

    这里从 ToolStack 只取两样东西, 都不是工具管线的一部分:

    - ``profile``: 用来兜底解析 Shell 可执行文件 (§6.1 的第二顺位).
    - ``barrier``: 回来之后清缓存.

    环境用 ``os.environ`` 的快照, **不是** ``tools.context_factory().environment`` ——
    后者是给 Agent 用的净化环境 (ADR-0014 §4.2), 拿它启动人工 Shell 会让用户的 alias,
    虚拟环境和 PATH 全部消失 (ADR-0017 §6.3).
    """
    service = ManualShellService(
        SystemShellResolver(tools.profile),
        build_interactive_shell_provider(),
        CliTerminalLease(console, renderer),
        ConsoleManualShellObserver(console),
        barrier=tools.barrier,
    )

    def _context() -> ManualShellContext:
        size = console.size
        return ManualShellContext(
            cwd=tools.context_factory().cwd,
            environment=MappingProxyType(dict(os.environ)),
            terminal=TerminalSize(columns=size.width, rows=size.height),
        )

    return ShellModeEntry(console, service, _context)


def _prompt_runtime_status(
    session: SessionService,
    config_service: ConfigService,
    llm_service: LlmConfigService,
    thinking_state: ThinkingRuntimeState | None = None,
) -> str:
    """输入框下方右侧的现读状态: 当前模式 + 项目当前模型 + 该模型的 thinking.

    模式取会话快照这个单一真相, 每帧现读, 所以 /plan 或 shift+tab 切换后下一帧就
    反映出来. 直接显示枚举值, 与 /status 输出和 mode_changed 事件 payload 同一套写法.
    """
    mode = session.current().mode.value
    return f"模式 {mode} · {_model_status(config_service, llm_service, thinking_state)}"


def _model_status(
    config_service: ConfigService,
    llm_service: LlmConfigService,
    thinking_state: ThinkingRuntimeState | None,
) -> str:
    """状态栏的模型段: 当前模型 + 该模型的 thinking."""
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


def run() -> ExitCode:
    """裸 forge 的产品入口; 返回进程退出码 (语义见 exit_codes 模块).

    两道环境前提 (非 root, 有 TTY) 一起在最前面拒掉: 它们与项目状态无关, 早拒可以
    保证任何一条拒绝路径都不碰 Forge home, 也让"同一个原因永远对应同一个退出码".
    """
    console = Console()
    # 日志先于一切装配: 下面每一条拒绝路径 (root, 无 TTY, 未信任, 项目被占) 都要留下
    # 记录, 而它们全都发生在还没有任何 service 的时候.
    status = start_logging()
    _log.info(
        "forge.start",
        entry="cli",
        version=__version__,
        cwd=str(Path.cwd()),
        log_file=None if status.file is None else str(status.file),
        log_level=status.level,
    )
    render_banner(console=console)

    # ADR-0009 决策 2: root 拆掉 OS 权限外墙, 安全模型不再成立, 故拒绝而非降级.
    # if is_elevated():
    #     console.print(_ELEVATED_REFUSAL)
    #     return ExitCode.ELEVATED

    # 交互式会话必须有真终端. 放在信任流程之前: 否则已信任的项目会一路装配到 REPL
    # 才发现没有 TTY —— 白占进程锁, 且同一个"没有 TTY"会因项目是否已信任而给出
    # 不同退出码.
    if not stdin_is_tty():
        _log.warning("forge.refused", reason="no_tty")
        console.print(_NO_TTY_REFUSAL)
        return ExitCode.NO_TTY

    service = _project_service()
    startup = WorkspaceStartup(service, TtyTrustPrompter(console))
    # 上面已确保有 TTY, 所以这里只可能是 bound / trusted / declined 三种结果.
    result = startup.resolve(Path.cwd(), interactive=True)
    if result.project is None:
        _log.warning("forge.refused", reason="untrusted", cwd=str(Path.cwd()))
        console.print("已取消：未信任当前目录，不创建任何配置。")
        return ExitCode.UNTRUSTED

    context = ProjectContext(result.project)
    _log.info(
        "project.resolved",
        project_id=context.project.project_id,
        workspace_roots=list(context.project.workspace_roots),
    )

    # 项目级排他：同一项目同一时刻只允许一个 forge 进程操作。锁文件落在用户级
    # Forge home 的项目目录下（不写进仓库），用 OS 咨询锁，进程崩溃由 OS 自动释放。
    lock = ProcessLock(
        config_dir() / "projects" / context.project.project_id / "forge.lock"
    )
    try:
        lock.acquire()
    except ProjectLockedError as exc:
        _log.warning("forge.refused", reason="project_locked", message=exc.message)
        console.print(f"[yellow]{exc.message}[/]")
        return ExitCode.PROJECT_LOCKED

    try:
        session = _session_service(context)
        # LLM 网关运行时（ADR-0011）：与 build_registry 共享同一批配置 service。
        # chat turn 由 AgentTurnService 驱动 BuiltinAgentLoop（ADR-0010）：模型
        # 调用经统一网关流式返回，增量进 REPL Live 区；usage 草稿随回复交回、
        # 由 AgentTurnService 落盘；Ctrl-C 经 TurnCancelSource 协作取消在途调用。
        project_home = config_dir() / "projects" / context.project.project_id
        forge_json = project_home / "forge.json"
        config_service = ConfigService(
            JsonConfigStore(config_file("config.json")),
            JsonConfigStore(forge_json),
        )
        llm_service = LlmConfigService(JsonLlmConfigStore(config_file("llm.json")))
        thinking_state = ThinkingRuntimeState()
        llm_runtime = build_llm_runtime(
            config_service, llm_service, forge_json, thinking_state
        )
        cancel_source = TurnCancelSource()
        run_bus = AgentRunEventBus()
        # turn 期间唯一的终端写入者 (ADR-0016 §8.1). 订阅在装配期完成, 生产路径
        # 不存在"建了总线却没有 subscriber"的状态.
        stream_view = TerminalRunRenderer(console)
        run_bus.subscribe(stream_view)

        # 工具, 安全与恢复三层. 装配它需要一个已经存在的会话 (审计要写进事件日志),
        # 所以放在 session 之后, registry 之前.
        tools = build_tool_stack(
            workspace_roots=tuple(context.project.workspace_roots),
            workspace_id=context.project.project_id,
            session=session,
            gateway=llm_runtime.gateway,
            run_bus=run_bus,
            approval=TtyApprovalService(console=console),
            toolchain_dirs=config_service.effective().toolchain_dirs,
        )

        # 上下文管理 (ADR-0032). 建在工具栈之后, 因为它必须拿到**同一个**
        # ArtifactStore —— 各建一个的话, 工具写进 A 的内容, 降级时 B 会说不存在,
        # 于是每一条降级占位都写成"已过期回收".
        #
        # 网关给它是为了二级摘要. 循环本来就持有网关, 所以这不新增任何能力 (ADR-0010
        # 允许循环调模型, 禁的是写事件).
        # 计量器与循环用的是**同一个** (ADR-0037): 压缩那次调用要和 Agent 自己的
        # 调用按同一套单价算钱, 各建一个迟早会出现两套价格.
        context_manager = ContextManager(
            artifacts=tools.artifacts,
            gateway=llm_runtime.gateway,
            meter=llm_runtime.usage_meter,
        )

        def _new_loop() -> BuiltinAgentLoop:
            return BuiltinAgentLoop(
                llm_runtime.gateway,
                llm_runtime.usage_meter,
                cancel_token_factory=cancel_source.current,
                event_bus=run_bus,
                context=context_manager,
            )

        def _is_git_repo(path: str) -> bool:
            """向上找 .git.

            它可能是目录 (普通仓库), 也可能是文件 (worktree / submodule).
            """
            return (Path(path) / ".git").exists()

        # 运行事实每轮现取: 用户可能刚 /add-dir 加过根, 也可能切了工作目录.
        # 投影在这里做 —— ExecutionProfile 带着受控 PATH 与环境白名单, 那些绝不进提示词
        # (ADR-0018 §4.3), 所以 builder 只能看见 RuntimeFacts 这一份脱敏投影.
        def _runtime_facts() -> RuntimeFacts:
            execution = tools.context_factory()
            return RuntimeFacts.from_profile(
                tools.profile,
                working_directory=execution.cwd,
                workspace_roots=execution.workspace_roots,
                # 判断是否是git仓库
                git_repository=_is_git_repo(execution.cwd),
            )

        agent_turn = AgentTurnService(
            session,
            loop_factory=_new_loop,
            prompt_builder=SystemPromptBuilder(),
            runtime_facts=_runtime_facts,
            instructions=FsProjectInstructionReader(),
            context_budget=llm_runtime.context_budget,
            context=context_manager,
            memory=tools.memory,
            planning=tools.planning,
            run_bus=run_bus,
            tools=tools.dispatcher,
            # 同一个屏障两处用: 人工 Shell 退出时 trip 它, agent turn 开始前查它
            # (ADR-0017 §10 / §12).
            barrier=tools.barrier,
        )
        # 人工 Shell (ADR-0017). 它与上面的工具栈**没有任何连接** —— 唯一的交点是
        # 那个失效屏障, 而屏障只做一件事: 让 Agent 不复用人工修改之前的事实.
        shell_mode = _build_shell_mode(console, stream_view, tools)
        output = RichOutput(console=console)
        presenter = RichMenuPresenter(console=console)
        picker = TtyDirectoryPicker(console=console)
        _log.info(
            "runtime.ready",
            session_id=session.current().session_id,
            model=str(config_service.effective().default_model),
            isolation=tools.profile.isolation_level.value,
            platform=tools.profile.platform,
            workspace_id=tools.workspace_id,
        )
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
            tools=tools,
            gateway_metrics=llm_runtime.gateway_metrics,
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
                session, config_service, llm_service, thinking_state
            ),
            shell_mode=shell_mode,
            plan_review=PlanReviewPrompt(
                console, tools.planning, PlanReviewService(tools.planning)
            ),
        ).run()
        _log.info("forge.stop", entry="cli", exit_code=ExitCode.OK.value)
        return ExitCode.OK
    finally:
        # 正常退出 / 异常 / Ctrl-C 都释放（flock 在 kill -9 时也由 OS 释放）。
        lock.release()
