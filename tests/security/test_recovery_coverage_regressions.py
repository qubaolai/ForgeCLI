"""恢复层曾经"声称可恢复而实际没有"的两处.

这类缺陷比拦不住更糟: 它让上层放心执行, 而撤销的时候才发现没有旧内容.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from forgecli.application.recovery.coordinator import (
    WorkspaceMutationCoordinator,
)
from forgecli.application.recovery.recovery_service import RecoveryService
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.recovery.checkpoint import (
    RecoveryPolicy,
    SnapshotStrategy,
)
from forgecli.domain.recovery.mutation import Operation
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    ExecutionContextRef,
    MovePair,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.infrastructure.execution.environment_probe import probe_execution_profile
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


def _plan(capabilities: frozenset[Capability], effects: PlanEffects) -> ToolPlan:
    return ToolPlan(
        plan_id="inv_1",
        tool_name="shell.run",
        spec_hash="spec",
        normalized_input={},
        capabilities=capabilities,
        effects=effects,
        target_resolution=(
            TargetResolution.FORGE_EXPANDED
            if effects.mutating_targets
            else TargetResolution.DYNAMIC
        ),
        workspace_scope=WorkspaceScope.IN_WORKSPACE,
        execution_context=ExecutionContextRef(cwd="/w", environment_hash="e"),
        declaration_confidence=DeclarationConfidence.DERIVED,
    )


def test_a_directory_records_metadata_instead_of_a_fake_empty_blob(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    coordinator = WorkspaceMutationCoordinator(FsRecoveryStore(tmp_path / "store"))
    plan = _plan(
        frozenset({Capability.WORKSPACE_WRITE}),
        PlanEffects(write_paths=(str(tmp_path / "pkg"),)),
    )
    transaction = coordinator.begin(
        plan,
        _context(tmp_path),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    assert transaction is not None

    entry = transaction.record_write(str(tmp_path / "pkg"), Operation.DELETE)

    assert entry.object_type.value == "directory"
    assert entry.preimage_content_hash is None
    assert entry.preimage_metadata_hash is not None
    assert entry.mode != 0


def test_full_strategy_reaches_files_in_subdirectories(tmp_path: Path) -> None:
    """FULL 必须递归到普通文件. 早先只列顶层一层, 而顶层大多是目录, 于是每个子树都
    一个字节没保护.
    """
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "deep" / "a.py").write_text("a\n")
    (tmp_path / "top.txt").write_text("t\n")
    # 可再生内容不该占预算: record_write 本来就不给它们存 preimage.
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"junk")

    coordinator = WorkspaceMutationCoordinator(
        FsRecoveryStore(tmp_path / "store"), policy=RecoveryPolicy()
    )
    # 会写但推不出目标 -> FULL.
    plan = _plan(frozenset({Capability.WORKSPACE_WRITE}), PlanEffects())
    assert coordinator.strategy_for(plan) is SnapshotStrategy.FULL

    transaction = coordinator.begin(
        plan,
        _context(tmp_path),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    assert transaction is not None

    protected = {
        entry.relative_path for entry in transaction.checkpoint.mutations.entries
    }
    assert "src/deep/a.py" in protected
    assert "top.txt" in protected
    assert not any(path.startswith("__pycache__") for path in protected)


def test_a_pure_reader_needs_no_checkpoint(tmp_path: Path) -> None:
    """修复不该让只读命令也去快照整个工作区 —— 那是纯粹的开销."""
    coordinator = WorkspaceMutationCoordinator(FsRecoveryStore(tmp_path / "store"))
    plan = _plan(
        frozenset({Capability.EXECUTE_SHELL, Capability.WORKSPACE_READ}),
        PlanEffects(read_paths=("/w/README.md",)),
    )
    assert plan.mutates_workspace is False
    assert coordinator.strategy_for(plan) is SnapshotStrategy.NONE


def _recovery_service(store: FsRecoveryStore) -> RecoveryService:
    def write(path: str, data: bytes) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    return RecoveryService(
        store,
        writer=write,
        remover=lambda path: Path(path).unlink(missing_ok=True),
        directory_creator=lambda path, mode: (
            Path(path).mkdir(parents=True, exist_ok=True),
            os.chmod(path, mode),
        ),
        mode_setter=lambda path, mode: os.chmod(path, mode),
    )


def test_deleted_empty_directories_and_file_modes_are_restored(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    empty = root / "pkg" / "empty"
    empty.mkdir(parents=True)
    source = root / "pkg" / "run.sh"
    source.write_text("echo ok\n", encoding="utf-8")
    source.chmod(0o755)
    store = FsRecoveryStore(tmp_path / "store")
    coordinator = WorkspaceMutationCoordinator(store)
    targets = (root / "pkg", empty, source)
    plan = _plan(
        frozenset({Capability.WORKSPACE_DELETE}),
        PlanEffects(delete_paths=tuple(str(path) for path in targets)),
    )
    transaction = coordinator.begin(
        plan,
        _context(root),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    assert transaction is not None
    for target in targets:
        transaction.record_write(str(target), Operation.DELETE)
    shutil.rmtree(root / "pkg")
    for target in targets:
        transaction.record_result(str(target), deleted=True)
    checkpoint = transaction.complete()

    outcome = _recovery_service(store).restore(checkpoint, _context(root))

    assert outcome.skipped == ()
    assert empty.is_dir()
    assert source.read_text(encoding="utf-8") == "echo ok\n"
    assert source.stat().st_mode & 0o777 == 0o755


def test_move_recovery_restores_source_and_removes_target(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    source = root / "source.txt"
    target = root / "target.txt"
    source.write_text("before", encoding="utf-8")
    store = FsRecoveryStore(tmp_path / "store")
    coordinator = WorkspaceMutationCoordinator(store)
    plan = _plan(
        frozenset({Capability.PATH_MOVE}),
        PlanEffects(move_pairs=(MovePair(source=str(source), target=str(target)),)),
    )
    transaction = coordinator.begin(
        plan,
        _context(root),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    assert transaction is not None
    transaction.record_write(str(source), Operation.MOVE)
    transaction.record_write(str(target), Operation.REPLACE)
    source.replace(target)
    transaction.record_result(str(source), deleted=True)
    transaction.record_result(str(target))
    checkpoint = transaction.complete()

    outcome = _recovery_service(store).restore(checkpoint, _context(root))

    assert outcome.skipped == ()
    assert source.read_text(encoding="utf-8") == "before"
    assert not target.exists()


def test_undo_never_removes_a_created_directory_after_user_added_a_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    created = root / "src"
    store = FsRecoveryStore(tmp_path / "store")
    coordinator = WorkspaceMutationCoordinator(store)
    plan = _plan(
        frozenset({Capability.WORKSPACE_WRITE}),
        PlanEffects(write_paths=(str(created),)),
    )
    transaction = coordinator.begin(
        plan,
        _context(root),
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    assert transaction is not None
    transaction.record_write(str(created), Operation.OVERWRITE)
    created.mkdir()
    transaction.record_result(str(created))
    checkpoint = transaction.complete()
    (created / "user.txt").write_text("user", encoding="utf-8")

    preview = _recovery_service(store).preview(checkpoint, _context(root))

    assert preview.conflicted
    assert preview.items[0].action == "skip_conflict"


def test_deleting_a_directory_takes_a_full_snapshot_not_a_targeted_one(
    tmp_path: Path,
) -> None:
    """`rm -rf src/` 的目标是封闭的, 但 TARGETED 救不了它.

    2026-08-21 实测发现的数据丢失: `_preimage_of` 对目录只存一个元数据哈希
    (mode + file_identity), 不存内容. 于是一次 `rm -rf src/` 拿到 TARGETED
    checkpoint, `restore` 报告 restored=('src',) 并重建出一个**空目录** —— 用户看到
    "已还原", 文件全没了.

    这个洞以前被"删目录必须人工确认"挡着. ADR-0030 让围栏内的删除自动放行之后, 它
    变成一次静默的数据丢失, 而围栏对此无能为力 —— 那是工作区内的操作.

    判据是**目标此刻是不是目录**, 不是"命令看起来危不危险".
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a\n")
    (tmp_path / "keep.txt").write_text("k\n")

    coordinator = WorkspaceMutationCoordinator(
        FsRecoveryStore(tmp_path / "store"), policy=RecoveryPolicy()
    )
    plan = _plan(
        frozenset({Capability.WORKSPACE_DELETE}),
        PlanEffects(delete_paths=(str(tmp_path / "src"),)),
    )
    context = _context(tmp_path)

    # 目标是封闭的, 所以不带 context 时仍然判 TARGETED —— 这正是旧行为.
    assert plan.target_resolution.closed
    assert coordinator.strategy_for(plan) is SnapshotStrategy.TARGETED
    # 带上文件系统视图之后看得见它是目录, 退到 FULL.
    assert coordinator.strategy_for(plan, context) is SnapshotStrategy.FULL

    transaction = coordinator.begin(
        plan,
        context,
        workspace_id="ws",
        session_id="s",
        turn_id="t",
        policy_version="1",
    )
    assert transaction is not None
    protected = {
        entry.relative_path for entry in transaction.checkpoint.mutations.entries
    }
    assert "src/a.py" in protected, "目录里的文件必须逐个进 checkpoint"


def test_deleting_a_plain_file_still_uses_the_cheap_targeted_path(
    tmp_path: Path,
) -> None:
    """反向确认: 别为了修目录那条把所有删除都拖进全量快照.

    全量快照实测 5000 文件 403ms, 20000 文件 1.7s (ADR-0030 实测记录). 每删一个文件
    都付这个代价是不可接受的.
    """
    (tmp_path / "a.py").write_text("a\n")
    coordinator = WorkspaceMutationCoordinator(FsRecoveryStore(tmp_path / "store"))
    plan = _plan(
        frozenset({Capability.WORKSPACE_DELETE}),
        PlanEffects(delete_paths=(str(tmp_path / "a.py"),)),
    )

    assert coordinator.strategy_for(plan, _context(tmp_path)) is (
        SnapshotStrategy.TARGETED
    )
