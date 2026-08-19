"""工具, 安全与恢复三层的组合根 (ADR-0004 §13).

三层在这里, 而且只在这里被串起来. 之所以单独一个模块而不是塞进 wiring.py: 这套装配的
顺序本身就是安全约束的一部分 —— 受保护路径要先于执行画像生成 (画像绑定 roots_hash),
恢复层要先于协调器 (协调器拿它建屏障), 分析器要先于授权服务. 装配顺序写乱了不会报错,
只会让某一层悄悄失效.

ExecutionContext 用工厂每次现取, 不缓存: 文件系统视图是带版本的快照, 跨调用复用会让
第二次调用基于过时的目录内容展开目标集合.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_run.tool_observer import EventBusToolRunObserver
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.manual_shell.mutation_barrier import ManualMutationBarrier
from forgecli.application.planning import PlanningService
from forgecli.application.recovery.coordinator import WorkspaceMutationCoordinator
from forgecli.application.recovery.recovery_service import RecoveryService
from forgecli.application.security.approval_service import ApprovalService
from forgecli.application.security.authorization_service import ToolAuthorizationService
from forgecli.application.security.classifier import (
    FailSafeClassifier,
    LlmSafetyClassifier,
)
from forgecli.application.security.learned_rules import LearnedRuleService
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.risk_cache import RiskCache
from forgecli.application.security.wiring import build_analyzer_registry
from forgecli.application.security.workspace_grants import WorkspaceGrants
from forgecli.application.session import SessionService
from forgecli.application.tool_request.coordinator import ToolRequestCoordinator
from forgecli.application.tool_request.dispatcher import CoordinatorToolDispatcher
from forgecli.application.tool_request.session_audit import SessionToolAudit
from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin import (
    DeleteTool,
    GitReadTool,
    ListFilesTool,
    MoveTool,
    PlanReadTool,
    PlanWriteTool,
    ReadFileTool,
    SearchTextTool,
    ShellRunTool,
    TodoReadTool,
    TodoSetStatusTool,
    TodoWriteTool,
    WritePatchTool,
)
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.execution.profile import ExecutionProfile
from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.infrastructure.config.paths import (
    artifacts_dir,
    learned_rules_file,
    plans_dir,
    recovery_dir,
)
from forgecli.infrastructure.execution.environment_probe import (
    build_execution_environment,
    probe_execution_profile,
)
from forgecli.infrastructure.execution.local_command_executor import (
    LocalCommandExecutor,
)
from forgecli.infrastructure.planning import FsPlanStore
from forgecli.infrastructure.recovery.cow_snapshot_backend import (
    probe_snapshot_backend,
)
from forgecli.infrastructure.recovery.fs_recovery_store import FsRecoveryStore
from forgecli.infrastructure.security.protected_paths_builder import (
    build_protected_path_policy,
)
from forgecli.infrastructure.security.toml_learned_rules_store import (
    TomlLearnedRuleStore,
)
from forgecli.infrastructure.tools.fs_artifact_store import FsArtifactStore
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView

__all__ = ["ToolStack", "build_tool_stack"]


@dataclass
class ToolStack:
    """装配好的一整套工具链. REPL 与 slash command 从这里取零件."""

    registry: ToolRegistry
    coordinator: ToolRequestCoordinator
    dispatcher: CoordinatorToolDispatcher
    recovery: RecoveryService
    grants: WorkspaceGrants
    protected_paths: ProtectedPathPolicy
    learned: LearnedRuleService
    profile: ExecutionProfile
    workspace_id: str
    context_factory: Callable[[], ExecutionContext]
    planning: PlanningService
    # 人工 Shell 回来之后要清的缓存都注册在它上面 (ADR-0017 §10).
    barrier: ManualMutationBarrier


def build_tool_stack(
    *,
    workspace_roots: tuple[str, ...],
    workspace_id: str,
    session: SessionService,
    gateway: LlmGateway,
    approval: ApprovalService | None = None,
    artifacts: ArtifactStore | None = None,
    run_bus: AgentRunEventBus,
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
    profile = probe_execution_profile(
        protected_roots_hash=protected.protected_roots_hash
    )
    environment = build_execution_environment(profile)

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

    # 3. 工具注册表.
    governor = ResourceGovernor()
    store = artifacts or FsArtifactStore(artifacts_dir())
    executor = LocalCommandExecutor()
    # 计划目录按会话分区, 而这里跑在组合根里 —— 那时 REPL 还没 session.start(),
    # 组合期读 current() 会直接抛 SessionStateError. 与下面分类器的 session id 同一个
    # 坑, 同样用延迟取.
    planning = PlanningService(
        FsPlanStore(lambda: plans_dir(workspace_id, session.current().session_id))
    )
    registry = ToolRegistry()
    registry.register_all(
        (
            PlanReadTool(planning),
            PlanWriteTool(planning),
            TodoReadTool(planning),
            TodoWriteTool(planning),
            TodoSetStatusTool(planning),
            ReadFileTool(governor, store),
            ListFilesTool(governor, store),
            SearchTextTool(governor, store),
            GitReadTool(executor, governor, store),
            WritePatchTool(_write_file),
            MoveTool(_move_file),
            DeleteTool(_delete_file),
            ShellRunTool(executor, governor, store),
        )
    )

    # 4. 安全层. 有 gateway 才有分类器; 没有就是 UnavailableSafetyClassifier ——
    #    脚本执行会因 CLASSIFIER_UNAVAILABLE 落 ASK. 这里绝不能放测试用的
    #    FakeSafetyClassifier: 它默认返回 LOW / allow, 会让"没接分类器"变成
    #    "分类器说没问题", 从而在生产里静默放行脚本.
    #
    #    session id 用 lambda 延迟取: 这个函数跑在组合根里, 那时 REPL 还没
    #    session.start(), 组合期读 current() 会直接抛 SessionStateError.
    classifier = FailSafeClassifier(
        LlmSafetyClassifier(
            gateway=gateway, session_id=lambda: session.current().session_id
        )
    )
    #    风险缓存显式持有: 人工 Shell 回来后必须清掉它 —— 缓存键里有脚本内容哈希,
    #    但用户可能改的是脚本**依赖**的文件, 那不在键里 (ADR-0017 §10).
    risk_cache = RiskCache()
    barrier = ManualMutationBarrier()
    barrier.register("risk_cache", risk_cache.clear)
    #    学习规则 (always) 由授权服务查, 由协调器写 —— 共用同一个实例.
    learned = LearnedRuleService(
        TomlLearnedRuleStore(learned_rules_file(workspace_id)),
        workspace_id=workspace_id,
    )
    authorization = ToolAuthorizationService(
        build_analyzer_registry(protected, classifier=classifier, cache=risk_cache),
        PolicyEngine(),
        learned=learned,
    )

    # 5. 恢复层. 快照后端启动时探测一次 —— 探测的方式是真的克隆一个临时文件,
    #    因为同一台机器上工作区与快照目录可能落在不同卷, 那时 clonefile / reflink
    #    直接失败, 与平台是什么无关. 拿不到就退回逐文件 preimage.
    recovery_store = FsRecoveryStore(recovery_dir())
    snapshots = probe_snapshot_backend(recovery_dir() / "snapshots", workspace_roots[0])
    mutations = WorkspaceMutationCoordinator(
        recovery_store, snapshots=snapshots, clock=_now
    )
    recovery = RecoveryService(
        recovery_store,
        writer=_write_bytes,
        remover=_delete_file,
        snapshots=snapshots,
        clock=_now,
    )

    coordinator = ToolRequestCoordinator(
        registry,
        ToolRuntime(registry),
        authorization,
        approval=approval,
        mutations=mutations,
        audit=SessionToolAudit(session),
        learned=learned,
        # 展示与审计走两条独立出口 (ADR-0016 §4.3): 观察者只发运行事件, 审计另有
        # SessionToolAudit. 订阅者抛异常被总线隔离, 因此终端出问题不会影响裁决与执行.
        observer=EventBusToolRunObserver(run_bus),
        workspace_id=workspace_id,
    )
    return ToolStack(
        registry=registry,
        coordinator=coordinator,
        dispatcher=CoordinatorToolDispatcher(coordinator, context_factory),
        recovery=recovery,
        grants=grants,
        protected_paths=protected,
        learned=learned,
        profile=profile,
        workspace_id=workspace_id,
        context_factory=context_factory,
        planning=planning,
        barrier=barrier,
    )


def _write_file(path: str | os.PathLike[str], content: str) -> None:
    """
    尽可能跨平台地原子替换文本文件。
    - target 要么保持旧内容，要么变成完整的新内容
    注意：
    - Windows 上若目标文件被其他程序以不允许删除/重命名的方式打开，
      os.replace() 仍可能失败。
    """
    target = Path(path)
    parent = target.parent

    parent.mkdir(parents=True, exist_ok=True)

    fd = -1
    temp_path: str | None = None

    try:
        fd, temp_path = tempfile.mkstemp(
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )

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


def _write_bytes(path: str, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.forge-partial")
    temp.write_bytes(data)
    temp.replace(target)


def _move_file(source: str, target: str) -> None:
    """移动文件. 目标父目录不存在时先建 —— prepare 已确认目标本身不存在."""
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    Path(source).replace(destination)


def _delete_file(path: str) -> None:
    """删文件或目录.

    原来只有 unlink, 目录会抛 IsADirectoryError —— fs.delete 因此永远删不掉目录.
    目录走 rmtree: 计划里已经把它展开成逐个文件并存过 preimage, 到这一步该删什么是
    确定的.
    """
    target = Path(path)
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=False)
        return
    target.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
