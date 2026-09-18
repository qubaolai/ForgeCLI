"""CLI 与 Web 共用的工具与权限组合根 (ADR-0054, ADR-0055).

装配顺序本身是约束的一部分: 受保护路径先于执行画像 (画像绑定 roots_hash), 围栏自测
先于沙箱设置 (设置里的 enabled 要与"真的立起来了"合成 active), 权限上下文先于工具池
(deny 规则决定池里有什么).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from forgecli.agent.run_bus import AgentRunEventBus
from forgecli.agent.tool_observer import EventBusToolRunObserver
from forgecli.core.app_state import AppState
from forgecli.core.execution.environment import EnvironmentInheritance
from forgecli.core.execution.fence import FencePolicy, fence_for
from forgecli.core.execution.profile import ExecutionProfile, IsolationLevel
from forgecli.core.permissions import (
    AdditionalWorkingDirectory,
    PermissionMode,
    PermissionRuleSource,
    ToolPermissionContext,
)
from forgecli.core.sandbox import SandboxSettings
from forgecli.core.tool.file_state import FileStateCache
from forgecli.core.tool.hashing import digest
from forgecli.core.tool.tool import Tool, ToolUseContext
from forgecli.execution.environment_probe import (
    build_execution_environment,
    probe_execution_profile,
)
from forgecli.execution.local_command_executor import LocalCommandExecutor
from forgecli.execution.sandbox.selection import select_provider
from forgecli.execution.sandboxed_command_executor import SandboxedCommandExecutor
from forgecli.human_interaction.service import (
    HumanPromptService,
    PendingHumanPromptService,
)
from forgecli.memory.entry import MemoryScope
from forgecli.memory.service import MemoryService
from forgecli.memory.store import JsonMemoryStore
from forgecli.planning.plan_store import FsPlanStore
from forgecli.planning.service import PlanningService
from forgecli.prompt.project_instruction_reader import FsProjectInstructionReader
from forgecli.security.auto_mode_classifier import AutoModeClassifier, StructuredQuery
from forgecli.security.interactive import make_can_use_tool
from forgecli.security.permission_setup import transition_permission_mode
from forgecli.security.permission_update import PermissionUpdateApplier
from forgecli.security.policy_rules import protected_read_paths
from forgecli.security.settings_store import (
    AutoModeRules,
    project_settings_file,
    read_auto_mode,
    read_sandbox,
    user_settings_file,
)
from forgecli.session.service import SessionService
from forgecli.settings.paths import (
    plans_dir,
    project_memory_file,
    projects_dir,
    user_memory_file,
)
from forgecli.tools.builtin.AskUserQuestion.tool import AskUserQuestionTool
from forgecli.tools.builtin.Bash.tool import BashTool
from forgecli.tools.builtin.Edit.tool import FileEditTool
from forgecli.tools.builtin.Glob.tool import GlobTool
from forgecli.tools.builtin.Grep.tool import GrepTool
from forgecli.tools.builtin.LSP.pool import LspServerPool
from forgecli.tools.builtin.LSP.tool import LspTool
from forgecli.tools.builtin.Test.tool import TestTool
from forgecli.tools.builtin.memory.tool import MemoryForgetTool, MemoryWriteTool
from forgecli.tools.builtin.planning.tool import (
    PlanReadTool,
    PlanWriteTool,
    TodoSetStatusTool,
    TodoWriteTool,
)
from forgecli.tools.builtin.Read.tool import FileReadTool
from forgecli.tools.builtin.Write.tool import FileWriteTool
from forgecli.tools.file_history import FileHistory
from forgecli.tools.hooks import HookRunner, load_hook_config
from forgecli.tools.runner import ToolRunner

__all__ = ["AppStateHolder", "ToolStack", "build_tool_stack"]


class AppStateHolder:
    """会话级可变状态的唯一持有者. 模式改了就通知会话服务, 两边不会各存一份."""

    def __init__(
        self,
        permission: ToolPermissionContext,
        sandbox: SandboxSettings,
        *,
        on_mode_change: Callable[[PermissionMode], None],
    ) -> None:
        self._state = AppState(tool_permission_context=permission, sandbox=sandbox)
        self._on_mode_change = on_mode_change

    def get(self) -> AppState:
        return self._state

    def set(self, state: AppState) -> None:
        previous = self._state.tool_permission_context.mode
        self._state = state
        if state.tool_permission_context.mode is not previous:
            self._on_mode_change(state.tool_permission_context.mode)

    @property
    def permission(self) -> ToolPermissionContext:
        return self._state.tool_permission_context

    def set_permission(self, permission: ToolPermissionContext) -> None:
        self.set(replace(self._state, tool_permission_context=permission))


@dataclass
class ToolStack:
    """装配好的一整套工具链, 界面适配器只从这里取公开协作件."""

    runner: ToolRunner
    app_state: AppStateHolder
    applier: PermissionUpdateApplier
    profile: ExecutionProfile
    workspace_id: str
    workspace_roots: tuple[str, ...]
    environment: Mapping[str, str]
    planning: PlanningService
    memory: MemoryService
    file_history: FileHistory
    lsp: LspServerPool

    @property
    def permission(self) -> ToolPermissionContext:
        return self.app_state.permission

    def set_mode(self, mode: PermissionMode) -> None:
        """三条入口 (Tab, /mode, POST /api/v1/mode) 都经这里; bypass / auto 没放开就抛,
        进出 auto 时剥 / 还危险的 allow 规则 (ADR-0058 决策 5)."""
        self.app_state.set_permission(transition_permission_mode(self.permission, mode))

    def cwd(self) -> str:
        return self.workspace_roots[0]

    def working_directories(self) -> tuple[str, ...]:
        roots = list(self.workspace_roots)
        for item in self.permission.additional_working_directories:
            if item.path not in roots:
                roots.append(item.path)
        return tuple(roots)

    def close(self) -> None:
        """项目关闭时收尾: 关掉语言服务器子进程."""
        self.lsp.close()


def _sandbox_settings(workspace_id: str, confined: bool) -> SandboxSettings:
    """用户级设置打底, 项目级覆盖; confined 来自围栏自测, 不是设置."""
    settings = read_sandbox(user_settings_file())
    project = read_sandbox(project_settings_file(workspace_id))
    if project is not None:
        settings = project
    if settings is None:
        settings = SandboxSettings()
    return replace(settings, confined=confined)


def _auto_mode_rules(workspace_id: str) -> AutoModeRules:
    """用户级在前, 项目级在后; 每次分类时读, 改了设置不必重启."""
    return read_auto_mode(user_settings_file()).merged_with(
        read_auto_mode(project_settings_file(workspace_id))
    )


def build_tool_stack(
    *,
    workspace_roots: tuple[str, ...],
    workspace_id: str,
    session: SessionService,
    permission: ToolPermissionContext,
    run_bus: AgentRunEventBus,
    prompts: HumanPromptService | None = None,
    environment_inheritance: EnvironmentInheritance = EnvironmentInheritance.ALL,
    non_interactive: bool = False,
    structured_query: StructuredQuery | None = None,
) -> ToolStack:
    """`structured_query` 是网关的 complete_structured: auto 模式的分类器靠它问模型
    (ADR-0058). 不给就没有分类器, auto 下的 ask 与 default 一样问人."""
    roots = tuple(dict.fromkeys(str(Path(root).resolve()) for root in workspace_roots))
    if not roots:
        raise ValueError("workspace_roots 不能为空")
    channel = prompts or PendingHumanPromptService()

    # 1. 受保护路径先生成: 执行画像要绑定它的哈希.
    protected = protected_read_paths()
    profile = probe_execution_profile(
        protected_roots_hash=digest(protected),
        workspace_roots=roots,
        inheritance=environment_inheritance,
    )
    environment = build_execution_environment(profile)

    # 2. 围栏: 选 Provider 并做行为自测 (ADR-0030 决策 3).
    provider, fence_report = select_provider()
    profile = replace(
        profile,
        isolation_level=(
            IsolationLevel.HOST_CONFINED
            if fence_report.confined
            else IsolationLevel.UNCONFINED
        ),
    )
    sandbox = _sandbox_settings(workspace_id, fence_report.confined)
    local = LocalCommandExecutor()
    sandboxed = SandboxedCommandExecutor(local, provider)

    def sync_mode(mode: PermissionMode) -> None:
        session.set_mode(mode)

    holder = AppStateHolder(permission, sandbox, on_mode_change=sync_mode)

    def fence_factory(context: ToolUseContext) -> FencePolicy | None:
        current = context.get_app_state()
        if not current.sandbox.active:
            return None
        extra = tuple(
            item.path
            for item in current.tool_permission_context.additional_working_directories
        )
        return fence_for(
            current.sandbox,
            workspace_roots=roots + extra,
            protected_paths=protected,
        )

    # 3. 各工具要的服务.
    planning = PlanningService(
        FsPlanStore(lambda: plans_dir(workspace_id, session.current().session_id))
    )
    memory = MemoryService(
        {
            MemoryScope.PROJECT: JsonMemoryStore(
                project_memory_file(workspace_id), MemoryScope.PROJECT
            ),
            MemoryScope.USER: JsonMemoryStore(user_memory_file(), MemoryScope.USER),
        }
    )
    lsp_pool = LspServerPool(roots[0], environment)
    project_home = projects_dir() / workspace_id
    file_history = FileHistory(backup_dir=project_home / "file-history")

    base_tools: tuple[Tool, ...] = (
        FileReadTool(),
        FileEditTool(),
        FileWriteTool(),
        GlobTool(),
        GrepTool(sandboxed if fence_report.confined else local, fence_factory),
        LspTool(lsp_pool),
        BashTool(
            local=local,
            sandboxed=sandboxed,
            shell_launch=profile.shell_launch,
            fence_factory=fence_factory,
        ),
        TodoWriteTool(planning),
        TodoSetStatusTool(planning),
        PlanReadTool(planning),
        PlanWriteTool(planning),
        AskUserQuestionTool(),
        MemoryWriteTool(memory),
        MemoryForgetTool(memory),
        TestTool(),
    )

    # 4. 权限: 建议的施加器, auto 模式的分类器与 can_use_tool.
    applier = PermissionUpdateApplier(
        user_file=user_settings_file(), project_file=project_settings_file(workspace_id)
    )
    hooks = HookRunner(
        load_hook_config((user_settings_file(), project_settings_file(workspace_id)))
    )
    classifier: AutoModeClassifier | None = None
    if structured_query is not None:
        instruction_reader = FsProjectInstructionReader()

        def instructions() -> str:
            # FORGE.md 是用户写给 Agent 的话, 分类器把它当用户意图的一部分.
            return "\n\n".join(item.text for item in instruction_reader.read(roots))

        classifier = AutoModeClassifier(
            structured_query,
            rules=lambda: _auto_mode_rules(workspace_id),
            instructions=instructions,
        )
    runner = ToolRunner(
        base_tools=base_tools,
        hooks=hooks,
        can_use_tool=make_can_use_tool(applier, classifier=classifier),
        get_app_state=holder.get,
        set_app_state=holder.set,
        prompts=channel,
        observer=EventBusToolRunObserver(run_bus),
        file_history=file_history,
        read_file_state=FileStateCache(),
        cwd=roots[0],
        environment=environment,
        workspace_roots=roots,
        tool_results_dir=lambda: str(
            project_home / "sessions" / session.current().session_id / "tool-results"
        ),
        internal_paths=(str(project_home / "sessions"),),
        non_interactive=non_interactive,
    )
    return ToolStack(
        runner=runner,
        app_state=holder,
        applier=applier,
        profile=profile,
        workspace_id=workspace_id,
        workspace_roots=roots,
        environment=environment,
        planning=planning,
        memory=memory,
        file_history=file_history,
        lsp=lsp_pool,
    )


def additional_directory(
    path: str, *, source: PermissionRuleSource
) -> AdditionalWorkingDirectory:
    return AdditionalWorkingDirectory(path=str(Path(path).resolve()), source=source)
