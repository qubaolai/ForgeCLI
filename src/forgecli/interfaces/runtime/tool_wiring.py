"""CLI 与 Web 共用的工具、安全与恢复组合根 (ADR-0004 §13).

三层在这里, 而且只在这里被串起来. 之所以单独一个模块而不是塞进 wiring.py: 这套装配的
顺序本身就是安全约束的一部分 —— 受保护路径要先于执行画像生成 (画像绑定 roots_hash),
恢复层要先于协调器 (协调器拿它建屏障), 分析器要先于授权服务. 装配顺序写乱了不会报错,
只会让某一层悄悄失效.

ExecutionContext 用工厂每次现取, 不缓存: 文件系统入口的版本标识调用实例，具体文件身份
由 ToolPlan 绑定。跨调用复用仍会让目录展开和上下文身份混在一起。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_run.tool_observer import EventBusToolRunObserver
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.memory.memory_service import MemoryService
from forgecli.application.planning.planning_service import PlanningService
from forgecli.application.recovery.coordinator import WorkspaceMutationCoordinator
from forgecli.application.recovery.recovery_service import RecoveryService
from forgecli.application.security.approval_service import ApprovalService
from forgecli.application.security.authorization_service import ToolAuthorizationService
from forgecli.application.security.learned_rules import LearnedRuleService
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.wiring import build_analyzer_registry
from forgecli.application.security.workspace_grants import WorkspaceGrants
from forgecli.application.session.session_service import SessionService
from forgecli.application.tool_request.coordinator import ToolRequestCoordinator
from forgecli.application.tool_request.dispatcher import CoordinatorToolDispatcher
from forgecli.application.tools.artifact_store import (
    ARTIFACT_RETENTION_SECONDS,
    ArtifactStore,
)
from forgecli.application.tools.builtin.artifact_read import ArtifactReadTool
from forgecli.application.tools.builtin.find_definition import FindDefinitionTool
from forgecli.application.tools.builtin.fs_apply_patch import ApplyPatchTool
from forgecli.application.tools.builtin.fs_find import FindTool
from forgecli.application.tools.builtin.fs_read import ReadFileTool
from forgecli.application.tools.builtin.git_read import GitReadTool
from forgecli.application.tools.builtin.memory_tools import (
    MemoryForgetTool,
    MemoryWriteTool,
)
from forgecli.application.tools.builtin.planning_tools import (
    PlanReadTool,
    PlanWriteTool,
    TodoSetStatusTool,
    TodoWriteTool,
)
from forgecli.application.tools.builtin.search_text import SearchTextTool
from forgecli.application.tools.builtin.shell_run import ShellRunTool
from forgecli.application.tools.command_executor import CommandExecutor
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.execution.environment import EnvironmentInheritance
from forgecli.domain.execution.fence import FencePolicy, fence_for
from forgecli.domain.execution.profile import ExecutionProfile, IsolationLevel
from forgecli.domain.intents import SessionMode
from forgecli.domain.memory.entry import MemoryScope
from forgecli.infrastructure.config.paths import (
    artifacts_dir,
    learned_rules_file,
    plans_dir,
    project_memory_file,
    recovery_dir,
    user_memory_file,
)
from forgecli.infrastructure.execution.environment_probe import (
    build_execution_environment,
    probe_execution_profile,
)
from forgecli.infrastructure.execution.local_command_executor import (
    LocalCommandExecutor,
)
from forgecli.infrastructure.execution.sandbox.selection import select_provider
from forgecli.infrastructure.execution.sandboxed_command_executor import (
    SandboxedCommandExecutor,
)
from forgecli.infrastructure.memory.json_memory_store import JsonMemoryStore
from forgecli.infrastructure.planning import FsPlanStore
from forgecli.infrastructure.recovery.cow_snapshot_backend import (
    probe_snapshot_backend,
)
from forgecli.infrastructure.recovery.fs_recovery_store import FsRecoveryStore
from forgecli.infrastructure.security.json_learned_rules_store import (
    JsonLearnedRuleStore,
)
from forgecli.infrastructure.security.protected_paths_builder import (
    build_protected_path_policy,
)
from forgecli.infrastructure.tools.fs_artifact_store import FsArtifactStore
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from forgecli.infrastructure.workspace.pygit2_git_queries import Pygit2GitQueries
from forgecli.shared.utils import now_iso

__all__ = ["ToolStack", "build_tool_stack"]


@dataclass
class ToolStack:
    """装配好的一整套工具链，界面适配器只从这里取公开协作件。"""

    registry: ToolRegistry
    dispatcher: CoordinatorToolDispatcher
    recovery: RecoveryService
    grants: WorkspaceGrants
    learned: LearnedRuleService
    profile: ExecutionProfile
    workspace_id: str
    context_factory: Callable[[], ExecutionContext]
    planning: PlanningService
    # 记忆 (ADR-0033 决策 10). 工具经它写, AgentTurnService 经它读 —— 必须是同一个
    # 实例, 否则模型这一轮记下的东西下一轮读不到.
    memory: MemoryService
    # 归档存储. 工具写进去, artifact_read 取回来, 所以它必须是**同一个实例** ——
    # 各建一个的话, 写在 A 里的内容 B 说不存在.
    #
    # 上下文管理不再需要它: 归档只在工具产出那一刻发生 (ADR-0041 决策 6), 窗口维护那一层
    # 不再回头去问"这份内容还取不取得回来".
    artifacts: ArtifactStore
    # 单条工具结果的内联上限. 窗口水位的构建期判据要用它 (ADR-0041 决策 8), 而只有装配层
    # 同时看得见它和当前模型的窗口.
    max_inline_bytes: int


def build_tool_stack(
    *,
    workspace_roots: tuple[str, ...],
    workspace_id: str,
    session: SessionService,
    gateway: LlmGateway,
    approval: ApprovalService | None = None,
    artifacts: ArtifactStore | None = None,
    run_bus: AgentRunEventBus,
    environment_inheritance: EnvironmentInheritance = EnvironmentInheritance.ALL,
) -> ToolStack:
    """按依赖顺序装配三层."""
    # 0. 工作区根先 resolve. macOS 上 /var 与 /tmp 都是指向 /private/... 的软链接,
    #    而路径归属判定看的是 realpath —— 根不规范化, 一个位于 /tmp 下的工作区会被
    #    判成"在工作区之外", 于是每次读自己的文件都要用户确认.
    roots = tuple(dict.fromkeys(str(Path(root).resolve()) for root in workspace_roots))
    if not roots:
        raise ValueError("workspace_roots 不能为空")
    workspace_roots = roots

    # 1. 受保护路径先生成: 执行画像要绑定它的哈希.
    protected = build_protected_path_policy(workspace_roots=workspace_roots)
    grants = WorkspaceGrants(protected)

    # 2. 执行画像与受控环境.
    # 工作区根传进去是为了把它们从继承的 PATH 里减掉: `node_modules/.bin` 与
    # `.venv/bin` 都在里面, 而那是 Agent 自己能写的目录.
    profile = probe_execution_profile(
        protected_roots_hash=protected.protected_roots_hash,
        workspace_roots=workspace_roots,
        inheritance=environment_inheritance,
    )
    environment = build_execution_environment(profile)

    # 2.5 围栏: 选 Provider 并做行为自测, 结论回填进画像 (ADR-0030 决策 3).
    #     自测结论必须进 execution_profile_hash —— 换一台没有围栏的机器继续用旧授权,
    #     就是"按有围栏批准, 按无围栏执行".
    provider, fence_report = select_provider()
    profile = replace(
        profile,
        isolation_level=(
            IsolationLevel.HOST_CONFINED
            if fence_report.confined
            else IsolationLevel.UNCONFINED
        ),
    )
    denied_reads = tuple(root.path for root in protected.roots if root.deny_read)

    def context_factory() -> ExecutionContext:
        return ExecutionContext(
            cwd=workspace_roots[0],
            workspace_roots=(*workspace_roots, *grants.as_roots()),
            environment=environment,
            # 每次新建视图: 上一次调用之后目录可能已经变了.
            filesystem=OsFileSystemView(),
            profile=profile,
            readonly_roots=grants.readonly_roots(),
        )

    def fence_factory(mode: SessionMode) -> FencePolicy:
        """按模式编译围栏 (ADR-0030 决策 4).

        与 context_factory 分开是因为它们的调用方不同: 撤销与展示路径也要
        ExecutionContext, 但它们不起子进程, 拿一个围栏策略没有意义. 围栏挂在真正知道
        mode 的那一层 —— 也就是调度器.
        """
        return fence_for(
            mode,
            workspace_roots=(*workspace_roots, *grants.as_roots()),
            readonly_roots=grants.readonly_roots(),
            protected_paths=denied_reads,
        )

    # 3. 工具注册表.
    governor = ResourceGovernor()
    store = artifacts or FsArtifactStore(artifacts_dir())
    # 启动时回收一次超期未引用的归档内容 (ADR-0032 决策 6). 挂在启动而不是会话结束:
    # 用户不会记得归档会话, 挂在"会话结束"上的回收等于不回收.
    #
    # 失败不阻塞启动: 回收不了的后果是多占一点磁盘, 而这是组合根, 抛出去就是起不来.
    with contextlib.suppress(OSError):
        store.sweep(older_than_seconds=ARTIFACT_RETENTION_SECONDS)
    # 围栏包在执行器外面, 不在工具内部分支 (ADR-0030 决策 1).
    executor: CommandExecutor = SandboxedCommandExecutor(
        LocalCommandExecutor(), provider
    )
    # 计划目录按会话分区, 而这里跑在组合根里 —— 那时还没 session.start(),
    # 组合期读 current() 会直接抛 SessionStateError. 与下面分类器的 session id 同一个
    # 坑, 同样用延迟取.
    planning = PlanningService(
        FsPlanStore(lambda: plans_dir(workspace_id, session.current().session_id))
    )
    # 记忆 (ADR-0033). 两级各一份存储: 项目事实跟项目走, 用户偏好跟人走.
    #
    # workspace_id 这个形参名是历史遗留, 传进来的就是 project_id (见 bootstrap 的
    # 装配点) —— 记忆按项目分区, 与 plans/ 用的是同一个身份.
    memory = MemoryService(
        {
            MemoryScope.PROJECT: JsonMemoryStore(
                project_memory_file(workspace_id), MemoryScope.PROJECT
            ),
            MemoryScope.USER: JsonMemoryStore(user_memory_file(), MemoryScope.USER),
        }
    )
    registry = ToolRegistry()
    registry.register_all(
        (
            PlanReadTool(planning),
            PlanWriteTool(planning),
            TodoWriteTool(planning),
            TodoSetStatusTool(planning),
            ArtifactReadTool(governor, store),
            MemoryWriteTool(memory),
            MemoryForgetTool(memory),
            FindTool(governor, store),
            ReadFileTool(governor, store),
            SearchTextTool(governor, store),
            FindDefinitionTool(governor, store),
            GitReadTool(Pygit2GitQueries(), governor, store),
            ApplyPatchTool(
                _create_file,
                _replace_file,
                _delete_file,
                _move_file,
                _make_directory,
            ),
            ShellRunTool(executor, governor, store),
        )
    )

    # 4. 安全层. ADR-0030 之后没有 LLM 分类器与风险缓存了: 它们的唯一调用点是脚本正文
    #    的风险分析, 而那一层随围栏落地整体删除. 裁决改由围栏边界决定, 不由"分类器说
    #    没问题"决定 —— 后者本来就带着非确定性与提示词注入两个问题.
    #    学习规则 (always) 由授权服务查, 由协调器写 —— 共用同一个实例.
    learned = LearnedRuleService(
        JsonLearnedRuleStore(learned_rules_file(workspace_id)),
        workspace_id=workspace_id,
    )
    authorization = ToolAuthorizationService(
        build_analyzer_registry(protected),
        PolicyEngine(),
        learned=learned,
    )

    # 5. 恢复层. 快照后端启动时探测一次 —— 探测的方式是真的克隆一个临时文件,
    #    因为同一台机器上工作区与快照目录可能落在不同卷, 那时 clonefile / reflink
    #    直接失败, 与平台是什么无关. 拿不到就退回逐文件 preimage.
    recovery_store = FsRecoveryStore(recovery_dir())
    snapshots = probe_snapshot_backend(recovery_dir() / "snapshots", workspace_roots[0])
    mutations = WorkspaceMutationCoordinator(
        recovery_store, snapshots=snapshots, clock=now_iso
    )
    recovery = RecoveryService(
        recovery_store,
        writer=_write_bytes,
        remover=_delete_file,
        directory_creator=_create_directory,
        mode_setter=_set_mode,
        snapshots=snapshots,
        clock=now_iso,
    )

    coordinator = ToolRequestCoordinator(
        registry,
        ToolRuntime(registry),
        authorization,
        approval=approval,
        mutations=mutations,
        learned=learned,
        # 订阅者抛异常被总线隔离, 因此展示侧出问题不会影响裁决与执行.
        observer=EventBusToolRunObserver(run_bus),
        workspace_id=workspace_id,
    )
    return ToolStack(
        registry=registry,
        dispatcher=CoordinatorToolDispatcher(
            coordinator, context_factory, fence_factory, confined=fence_report.confined
        ),
        recovery=recovery,
        grants=grants,
        learned=learned,
        profile=profile,
        workspace_id=workspace_id,
        context_factory=context_factory,
        planning=planning,
        memory=memory,
        artifacts=store,
        max_inline_bytes=governor.max_inline_bytes,
    )


def _replace_file(path: str | os.PathLike[str], content: str) -> None:
    """
    尽可能跨平台地原子替换文本文件。
    - target 要么保持旧内容，要么变成完整的新内容
    注意：
    - Windows 上若目标文件被其他程序以不允许删除/重命名的方式打开，
      os.replace() 仍可能失败。
    """
    target = Path(path)
    parent = target.parent

    if not parent.is_dir():
        raise FileNotFoundError(f"父目录不存在或不是目录: {parent}")
    before = target.lstat()
    if stat.S_ISLNK(before.st_mode):
        raise OSError(f"拒绝替换符号链接: {target}")

    fd = -1
    temp_path: str | None = None

    try:
        fd, temp_path = tempfile.mkstemp(
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )

        if hasattr(os, "fchmod"):
            os.fchmod(fd, stat.S_IMODE(before.st_mode))

        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            fd = -1

            f.write(content)

            # Python -> OS
            f.flush()

            # OS -> storage device
            os.fsync(f.fileno())

        # 同一文件系统内用新文件原子替换旧路径
        os.replace(temp_path, target)
        temp_path = None

        # POSIX 上进一步保证目录项落盘。
        # Windows 不支持以这种方式 fsync 目录。
        if os.name != "nt":
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

    finally:
        if fd != -1:
            os.close(fd)

        if temp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)


def _create_file(path: str | os.PathLike[str], content: str) -> None:
    """用硬链接发布完整临时文件，目标已存在时由内核原子拒绝。"""
    target = Path(path)
    parent = target.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"父目录不存在或不是目录: {parent}")
    fd = -1
    temp_path: str | None = None
    try:
        fd, temp_path = tempfile.mkstemp(
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            fd = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp_path, target)
        os.unlink(temp_path)
        temp_path = None
        _sync_directory(parent)
    finally:
        if fd != -1:
            os.close(fd)
        if temp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)


def _write_bytes(path: str, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.forge-partial")
    temp.write_bytes(data)
    temp.replace(target)


def _move_file(source: str, target: str) -> None:
    """以 no-replace 语义移动普通文件，目标竞争出现时绝不覆盖。"""
    destination = Path(target)
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"目标父目录不存在或不是目录: {destination.parent}")
    source_path = Path(source)
    if source_path.is_symlink():
        raise OSError(f"拒绝移动符号链接: {source}")
    os.link(source_path, destination)
    try:
        source_path.unlink()
    except OSError:
        with contextlib.suppress(OSError):
            destination.unlink()
        raise
    _sync_directory(destination.parent)
    if source_path.parent != destination.parent:
        _sync_directory(source_path.parent)


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _create_directory(path: str, mode: int) -> None:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    if mode:
        _set_mode(path, mode)


def _make_directory(path: str) -> None:
    # exist_ok: 一个信封里的多个段各自在 prepare 阶段算父目录, 算的都是"这一段执行前"
    # 的磁盘状态. 前一段建过 backend/ 之后, 后一段的计划里仍然写着要建它 —— 报
    # FileExistsError 会让整封补丁停在第一段, 剩下的文件既没写也没人知道.
    #
    # parents 保持默认的 False: 要建的每一级都由 _missing_parents 逐级列进
    # write_paths 并经过裁决, parents=True 会创建没被声明过的祖先目录.
    Path(path).mkdir(exist_ok=True)


def _set_mode(path: str, mode: int) -> None:
    os.chmod(path, stat.S_IMODE(mode), follow_symlinks=False)


def _delete_file(path: str) -> None:
    """删文件或目录.

    原来只有 unlink, 目录会抛 IsADirectoryError —— fs_delete 因此永远删不掉目录.
    目录走 rmtree: 计划里已经把它展开成逐个文件并存过 preimage, 到这一步该删什么是
    确定的.
    """
    target = Path(path)
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=False)
        return
    target.unlink(missing_ok=True)
