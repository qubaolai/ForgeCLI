"""恢复相关的 slash command: /checkpoints, /undo, /restore, /recovery (ADR-0015 §11).

三条界面上的硬性要求:

- **默认不覆盖后续修改.** /undo 遇到冲突项直接跳过并说明, 用户要覆盖得显式再来一次.
- **预览先于执行.** /restore <id> --preview 只看不动.
- **恢复数据不进模型上下文.** 这些命令的输出给用户看, 不作为 observation 回填.

四条命令都不进工具目录, 只能由人发起: 让 Agent 能撤销自己刚做的事, 等于给它一条抹掉
证据的通道.
"""

from __future__ import annotations

from collections.abc import Callable

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.recovery.recovery_service import RecoveryService
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.intents import SlashCommand
from forgecli.domain.recovery.checkpoint import RecoveryCheckpoint

__all__ = [
    "CheckpointsCommand",
    "RecoveryStatusCommand",
    "RestoreCommand",
    "UndoCommand",
]


class CheckpointsCommand(CommandHandler):
    """列出本工作区的恢复点."""

    def __init__(
        self,
        service: RecoveryService,
        workspace_id: str,
        output: UserOutput,
    ) -> None:
        self._service = service
        self._workspace_id = workspace_id
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        checkpoints = self._service.list_checkpoints(self._workspace_id)
        if not checkpoints:
            self._output.print("暂无恢复点.")
            return False
        for item in checkpoints:
            self._output.print(_summary(item))
        return False


class _RollbackCommand(CommandHandler):
    """/undo 与 /restore 共用的执行体.

    两条命令的差别只有一处: **默认目标**. /undo 不带参数时取最近一个恢复点, /restore
    必须写出 checkpoint_id. 这个差别是有意的 —— "撤销刚才那步"是高频动作, 而"回到某个
    特定恢复点"跨度可能很大, 不该有默认值让人误触.
    """

    def __init__(
        self,
        service: RecoveryService,
        workspace_id: str,
        context_factory: Callable[[], ExecutionContext],
        output: UserOutput,
    ) -> None:
        self._service = service
        self._workspace_id = workspace_id
        self._context_factory = context_factory
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        checkpoints = self._service.list_checkpoints(self._workspace_id)
        if not checkpoints:
            self._output.print(self._empty_message)
            return False
        target = self._target(checkpoints, command.args)
        if target is None:
            return False

        context = self._context_factory()
        if "--preview" in command.args:
            # 预览先于执行 (ADR-0015 §11): 只算不动, 冲突项在这一步就能看见.
            preview = self._service.preview(target, context)
            for item in preview.items:
                self._output.print(
                    f"{item.action}\t{item.relative_path}\t{item.detail}"
                )
            if not preview.clean:
                self._output.print(
                    f"其中 {len(preview.conflicted)} 项已被后续修改, 执行时会跳过."
                )
            return False

        outcome = self._service.restore(target, context)
        self._output.print(
            f"已还原 {len(outcome.restored)} 项, 跳过 {len(outcome.skipped)} 项."
        )
        for path in outcome.skipped:
            # 跳过的都是冲突项: 告诉用户为什么没动它, 而不是假装成功.
            self._output.print(f"  冲突 (执行后又被修改, 未覆盖): {path}")
        return True

    @property
    def _empty_message(self) -> str:
        raise NotImplementedError

    def _target(
        self, checkpoints: tuple[RecoveryCheckpoint, ...], args: tuple[str, ...]
    ) -> RecoveryCheckpoint | None:
        raise NotImplementedError


class UndoCommand(_RollbackCommand):
    """撤销最近一次 (或指定的) 工具调用."""

    @property
    def _empty_message(self) -> str:
        return "没有可撤销的操作."

    def _target(
        self, checkpoints: tuple[RecoveryCheckpoint, ...], args: tuple[str, ...]
    ) -> RecoveryCheckpoint | None:
        wanted = _positional(args)
        if not wanted:
            return checkpoints[0]
        found = _find(checkpoints, wanted[0])
        if found is None:
            self._output.print(f"找不到恢复点: {wanted[0]}")
        return found


class RestoreCommand(_RollbackCommand):
    """回到指定恢复点 (ADR-0015 §11).

    自身也会建一个恢复点 (由 RecoveryService.restore 负责), 所以恢复错了还能再恢复
    回去 —— 否则 /restore 本身就成了一个不可撤销的破坏性操作.
    """

    @property
    def _empty_message(self) -> str:
        return "暂无恢复点."

    def _target(
        self, checkpoints: tuple[RecoveryCheckpoint, ...], args: tuple[str, ...]
    ) -> RecoveryCheckpoint | None:
        wanted = _positional(args)
        if not wanted:
            self._output.print(
                "用法: /restore <checkpoint_id> [--preview]  (用 /checkpoints 查看)"
            )
            return None
        found = _find(checkpoints, wanted[0])
        if found is None:
            self._output.print(f"找不到恢复点: {wanted[0]}")
        return found


class RecoveryStatusCommand(CommandHandler):
    """展示恢复层状态, 包含崩溃后可能留下部分修改的事务."""

    def __init__(
        self,
        service: RecoveryService,
        workspace_id: str,
        output: UserOutput,
    ) -> None:
        self._service = service
        self._workspace_id = workspace_id
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        checkpoints = self._service.list_checkpoints(self._workspace_id)
        pending = self._service.crash_recovery_candidates(self._workspace_id)
        self._output.print(f"恢复点总数: {len(checkpoints)}")
        if not pending:
            self._output.print("没有未收尾的事务.")
            return False
        self._output.print(f"发现 {len(pending)} 个未收尾事务 (可能已发生部分修改):")
        for item in pending:
            self._output.print(f"  {_summary(item)}")
        return False


def _positional(args: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(arg for arg in args if not arg.startswith("--"))


def _find(
    checkpoints: tuple[RecoveryCheckpoint, ...], wanted: str
) -> RecoveryCheckpoint | None:
    """按 checkpoint_id 或 tool_invocation_id 定位. 两者都接受: 用户从 /checkpoints
    抄的是前者, 从工具执行记录里抄的是后者."""
    return next(
        (
            item
            for item in checkpoints
            if wanted in (item.checkpoint_id, item.tool_invocation_id)
        ),
        None,
    )


def _summary(checkpoint: RecoveryCheckpoint) -> str:
    return (
        f"{checkpoint.checkpoint_id}  {checkpoint.status.value:<10}"
        f"  {checkpoint.snapshot_strategy.value:<8}"
        f"  {len(checkpoint.mutations)} 项变更  {checkpoint.created_at}"
    )
