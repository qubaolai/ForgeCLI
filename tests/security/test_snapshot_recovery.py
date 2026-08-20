"""写时复制快照式恢复.

它解决的是一个具体的死结: 复制式 preimage 按文件保存旧内容, 于是"可能写到工作区任何
位置"要求先复制整个工作区, 而真实仓库轻易超过预算 —— 最需要恢复保障的那类命令
(`npm test`, `make`, 影响范围推导不出来的程序) 因此被直接拒绝执行.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pytest import mark, raises

from forgecli.application.recovery.coordinator import (
    RecoveryUnavailableError,
    WorkspaceMutationCoordinator,
)
from forgecli.application.recovery.recovery_service import RecoveryService
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.recovery.checkpoint import RecoveryPolicy, SnapshotStrategy
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import (
    ExecutionContextRef,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.infrastructure.execution.environment_probe import probe_execution_profile
from forgecli.infrastructure.recovery.cow_snapshot_backend import (
    CowSnapshotBackend,
    probe_snapshot_backend,
)
from forgecli.infrastructure.recovery.fs_recovery_store import FsRecoveryStore
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView


def _context(root: Path) -> ExecutionContext:
    return ExecutionContext(
        cwd=str(root),
        workspace_roots=(str(root),),
        environment={},
        filesystem=OsFileSystemView(),
        profile=probe_execution_profile(protected_roots_hash="h"),
    )


def _full_plan() -> ToolPlan:
    """会写但推不出目标 -> FULL. `npm test` 就是这个形状."""
    return ToolPlan(
        plan_id="inv_1",
        tool_name="shell.run",
        spec_hash="spec",
        normalized_input={"command": "npm test"},
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
        effects=PlanEffects(),
        target_resolution=TargetResolution.DYNAMIC,
        workspace_scope=WorkspaceScope.IN_WORKSPACE,
        execution_context=ExecutionContextRef(cwd="/w", environment_hash="e"),
    )


def _populate(root: Path, file_count: int) -> None:
    for index in range(file_count):
        target = root / f"pkg{index % 20}" / f"mod{index}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"value = {index}\n")


def _backend(tmp_path: Path, workspace: Path) -> CowSnapshotBackend | None:
    return probe_snapshot_backend(tmp_path / "snapshots", str(workspace))


def test_without_a_snapshot_backend_a_big_workspace_is_refused(tmp_path: Path) -> None:
    """没有快照后端时结论必须是"建不起恢复点", 而不是"保护一部分算了"."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _populate(workspace, 60)

    coordinator = WorkspaceMutationCoordinator(
        FsRecoveryStore(tmp_path / "store"),
        policy=RecoveryPolicy(max_checkpoint_files=10),
        snapshots=None,
    )
    assert coordinator.strategy_for(_full_plan()) is SnapshotStrategy.FULL

    with raises(RecoveryUnavailableError, match="恢复预算不足"):
        coordinator.begin(
            _full_plan(),
            _context(workspace),
            workspace_id="ws",
            session_id="s",
            turn_id="t",
            policy_version="1",
        )


def test_a_snapshot_backend_lifts_the_file_budget(tmp_path: Path) -> None:
    """快照的代价是元数据而不是内容, 所以逐文件预算对它不适用."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _populate(workspace, 60)
    backend = _backend(tmp_path, workspace)
    if backend is None:
        return  # 这个卷不支持写时复制; 退路已由上一条测试覆盖.

    coordinator = WorkspaceMutationCoordinator(
        FsRecoveryStore(tmp_path / "store"),
        # 上限故意压到远低于文件数: 有快照就不该再走这条预算.
        policy=RecoveryPolicy(max_checkpoint_files=10),
        snapshots=backend,
    )
    transaction = coordinator.begin(
        _full_plan(),
        _context(workspace),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )

    assert transaction is not None
    checkpoint = transaction.checkpoint
    assert checkpoint.snapshot_ref is not None
    assert checkpoint.snapshot_backend == "copy_on_write"
    # 有快照就不再逐文件存 preimage.
    assert checkpoint.mutations.entries == ()


def test_a_snapshot_restores_modified_created_and_deleted_files(tmp_path: Path) -> None:
    """三种变化都要回到快照那一刻.

    新建的文件必须被删掉 —— 少了这一步, "撤销"之后那次调用留下的东西还在树里, 而用户
    以为已经回到原状.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("original\n")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "gone.txt").write_text("will be deleted\n")
    backend = _backend(tmp_path, workspace)
    if backend is None:
        return

    handle = backend.capture(str(workspace), "ckpt_1")

    (workspace / "keep.txt").write_text("tampered\n")
    (workspace / "sub" / "gone.txt").unlink()
    (workspace / "created.txt").write_text("should not survive undo\n")

    backend.restore(handle, str(workspace))

    assert (workspace / "keep.txt").read_text() == "original\n"
    assert (workspace / "sub" / "gone.txt").read_text() == "will be deleted\n"
    assert not (workspace / "created.txt").exists()


def test_the_service_routes_a_snapshot_checkpoint_to_the_backend(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("before\n")
    backend = _backend(tmp_path, workspace)
    if backend is None:
        return

    store = FsRecoveryStore(tmp_path / "store")
    coordinator = WorkspaceMutationCoordinator(store, snapshots=backend)
    transaction = coordinator.begin(
        _full_plan(),
        _context(workspace),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    assert transaction is not None
    (workspace / "a.txt").write_text("after\n")
    checkpoint = transaction.complete()

    service = RecoveryService(
        store,
        writer=lambda path, data: Path(path).write_bytes(data),
        remover=lambda path: Path(path).unlink(missing_ok=True),
        snapshots=backend,
    )
    outcome = service.restore(checkpoint, _context(workspace))

    assert outcome.skipped == ()
    assert (workspace / "a.txt").read_text() == "before\n"


def test_a_snapshot_checkpoint_without_a_backend_refuses_to_restore(
    tmp_path: Path,
) -> None:
    """声称能还原却还原不了, 比明确报错糟得多."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("before\n")
    backend = _backend(tmp_path, workspace)
    if backend is None:
        return

    store = FsRecoveryStore(tmp_path / "store")
    coordinator = WorkspaceMutationCoordinator(store, snapshots=backend)
    transaction = coordinator.begin(
        _full_plan(),
        _context(workspace),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    assert transaction is not None
    checkpoint = transaction.complete()

    service = RecoveryService(
        store,
        writer=lambda path, data: Path(path).write_bytes(data),
        remover=lambda path: Path(path).unlink(missing_ok=True),
        snapshots=None,
    )
    with raises(RecoveryUnavailableError, match="没有可用的快照后端"):
        service.restore(checkpoint, _context(workspace))


@mark.parametrize("strategy", [SnapshotStrategy.TARGETED, SnapshotStrategy.NONE])
def test_only_full_captures_a_snapshot(
    tmp_path: Path, strategy: SnapshotStrategy
) -> None:
    """TARGETED 已经有精确目标, 为几个文件快照整棵树是纯浪费."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("x\n")
    backend = _backend(tmp_path, workspace)
    if backend is None:
        return

    coordinator = WorkspaceMutationCoordinator(
        FsRecoveryStore(tmp_path / "store"), snapshots=backend
    )
    plan = ToolPlan(
        plan_id="inv_2",
        tool_name="fs.edit_file",
        spec_hash="spec",
        normalized_input={},
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
        effects=(
            PlanEffects(write_paths=(str(workspace / "a.txt"),))
            if strategy is SnapshotStrategy.TARGETED
            else PlanEffects()
        ),
        target_resolution=(
            TargetResolution.STATIC
            if strategy is SnapshotStrategy.TARGETED
            else TargetResolution.STATIC
        ),
        workspace_scope=WorkspaceScope.IN_WORKSPACE,
        execution_context=ExecutionContextRef(cwd="/w", environment_hash="e"),
    )
    if strategy is SnapshotStrategy.NONE:
        plan = ToolPlan(
            plan_id="inv_3",
            tool_name="fs.read_file",
            spec_hash="spec",
            normalized_input={},
            capabilities=frozenset({Capability.WORKSPACE_READ}),
            effects=PlanEffects(read_paths=(str(workspace / "a.txt"),)),
            target_resolution=TargetResolution.STATIC,
            workspace_scope=WorkspaceScope.IN_WORKSPACE,
            execution_context=ExecutionContextRef(cwd="/w", environment_hash="e"),
        )

    assert coordinator.strategy_for(plan) is strategy
    transaction = coordinator.begin(
        plan,
        _context(workspace),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    if strategy is SnapshotStrategy.NONE:
        assert transaction is None
    else:
        assert transaction is not None
        assert transaction.checkpoint.snapshot_ref is None


def test_expired_snapshots_are_pruned(tmp_path: Path) -> None:
    """快照占的磁盘接近零, 但 inode 不是零 —— 不清理会把卷的 inode 用光.

    `retention_seconds` 早先是 RecoveryPolicy 上一个没有任何执行点的字段.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("x\n")
    backend = _backend(tmp_path, workspace)
    if backend is None:
        return

    store = FsRecoveryStore(tmp_path / "store")
    coordinator = WorkspaceMutationCoordinator(
        store,
        policy=RecoveryPolicy(retention_seconds=60.0),
        snapshots=backend,
        clock=lambda: "2026-08-10T00:00:00+00:00",
    )
    transaction = coordinator.begin(
        _full_plan(),
        _context(workspace),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    assert transaction is not None
    transaction.complete()
    location = Path(transaction.checkpoint.snapshot_ref or "")
    assert location.is_dir()

    created = datetime.fromisoformat("2026-08-10T00:00:00+00:00").timestamp()
    # 还在保留期内: 一条都不清.
    assert coordinator.prune_expired("ws", now_epoch=created + 30) == 0
    assert location.is_dir()

    # 过了保留期: manifest 与快照一起清掉.
    assert coordinator.prune_expired("ws", now_epoch=created + 120) == 1
    assert not location.exists()
    assert store.list_checkpoints("ws") == ()
