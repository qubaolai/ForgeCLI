"""/undo 与 /restore 的参数语义 (ADR-0015 §11).

用假的 RecoveryService: 这里要验的是命令层的取舍 —— **选中哪个恢复点, 什么时候只看
不动** —— 而不是还原引擎本身. 引擎的冲突判定有自己的用例.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from rich.console import Console

from forgecli.domain.intents import SlashCommand
from forgecli.interfaces.cli.commands.recovery_command import (
    RestoreCommand,
    UndoCommand,
)
from forgecli.interfaces.cli.output import RichOutput


@dataclass(frozen=True)
class _Checkpoint:
    checkpoint_id: str
    tool_invocation_id: str
    status: object = type("S", (), {"value": "committed"})()
    snapshot_strategy: object = type("S", (), {"value": "targeted"})()
    mutations: object = type("M", (), {"entries": ()})()
    created_at: str = "2026-08-04T10:00:00"


@dataclass(frozen=True)
class _Item:
    relative_path: str
    action: str
    detail: str = ""


@dataclass
class _Preview:
    items: tuple[_Item, ...]
    conflicted: tuple[_Item, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.conflicted


@dataclass
class _Outcome:
    restored: tuple[str, ...]
    skipped: tuple[str, ...]


@dataclass
class _Service:
    checkpoints: tuple[_Checkpoint, ...]
    previewed: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)

    def list_checkpoints(self, workspace_id: str) -> tuple[_Checkpoint, ...]:
        return self.checkpoints

    def preview(self, checkpoint: _Checkpoint, context: object) -> _Preview:
        self.previewed.append(checkpoint.checkpoint_id)
        return _Preview(items=(_Item("a.py", "restore_content"),))

    def restore(self, checkpoint: _Checkpoint, context: object) -> _Outcome:
        self.restored.append(checkpoint.checkpoint_id)
        return _Outcome(restored=("a.py",), skipped=())


CHECKPOINTS = (
    _Checkpoint("cp-newest", "inv-3"),
    _Checkpoint("cp-middle", "inv-2"),
    _Checkpoint("cp-oldest", "inv-1"),
)


def _run(command_class: type, service: _Service, args: tuple[str, ...]) -> str:
    console = Console(file=io.StringIO(), width=120, no_color=True)
    handler = command_class(
        service,
        "ws-1",
        lambda: object(),
        RichOutput(console),
    )
    handler.execute(SlashCommand("/x", "x", args))
    stream = console.file
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()


# ---- /undo ----


def test_undo_without_args_takes_the_newest_checkpoint() -> None:
    service = _Service(CHECKPOINTS)

    _run(UndoCommand, service, ())

    assert service.restored == ["cp-newest"]


def test_undo_accepts_a_tool_invocation_id() -> None:
    """用户手边有的往往是工具执行记录里的 invocation id, 不是 checkpoint id."""
    service = _Service(CHECKPOINTS)

    _run(UndoCommand, service, ("inv-2",))

    assert service.restored == ["cp-middle"]


def test_undo_preview_does_not_restore() -> None:
    service = _Service(CHECKPOINTS)

    _run(UndoCommand, service, ("--preview",))

    assert service.previewed == ["cp-newest"]
    assert service.restored == []


# ---- /restore ----


def test_restore_refuses_to_guess_a_target() -> None:
    """ "回到某个恢复点"跨度可能很大, 不该有默认值让人误触."""
    service = _Service(CHECKPOINTS)

    text = _run(RestoreCommand, service, ())

    assert service.restored == []
    assert "用法" in text


def test_restore_takes_the_named_checkpoint() -> None:
    service = _Service(CHECKPOINTS)

    _run(RestoreCommand, service, ("cp-oldest",))

    assert service.restored == ["cp-oldest"]


def test_restore_preview_only_looks() -> None:
    service = _Service(CHECKPOINTS)

    _run(RestoreCommand, service, ("cp-oldest", "--preview"))

    assert service.previewed == ["cp-oldest"]
    assert service.restored == []


def test_an_unknown_checkpoint_restores_nothing() -> None:
    service = _Service(CHECKPOINTS)

    text = _run(RestoreCommand, service, ("cp-nope",))

    assert service.restored == []
    assert "找不到恢复点" in text


def test_no_checkpoints_at_all_is_not_an_error() -> None:
    service = _Service(())

    text = _run(RestoreCommand, service, ("cp-oldest",))

    assert service.restored == []
    assert "暂无恢复点" in text


def test_conflicts_are_called_out_in_the_preview() -> None:
    """冲突项在预览里就要能看见, 否则"预览先于执行"只预览了乐观路径."""
    service = _Service(CHECKPOINTS)
    conflicted = _Item("b.py", "skip_conflict", "执行后又被修改")
    service.preview = lambda checkpoint, context: _Preview(  # type: ignore[assignment]
        items=(conflicted,), conflicted=(conflicted,)
    )

    text = _run(RestoreCommand, service, ("cp-oldest", "--preview"))

    assert "1 项已被后续修改" in text
