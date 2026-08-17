"""恢复层曾经"声称可恢复而实际没有"的两处.

这类缺陷比拦不住更糟: 它让上层放心执行, 而撤销的时候才发现没有旧内容.
"""

from __future__ import annotations

from pathlib import Path

from pytest import raises

from forgecli.application.recovery.coordinator import (
    RecoveryUnavailableError,
    WorkspaceMutationCoordinator,
)
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


def test_a_directory_never_yields_a_silent_empty_preimage(tmp_path: Path) -> None:
    """目录进不了 preimage. 早先 read_bytes 吞掉 OSError 返回 b"", 于是 checkpoint 里
    存的是"空内容的哈希", recoverability 却标着 FULL —— 声称可恢复而什么都没存.
    """
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

    with raises(RecoveryUnavailableError, match="不是普通文件"):
        transaction.record_write(str(tmp_path / "pkg"), Operation.OVERWRITE)


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
