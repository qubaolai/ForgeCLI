from __future__ import annotations

from pathlib import Path

from forgecli.application.tools.builtin.fs_move import MoveTool
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResultStatus
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE


def _context(root: Path) -> ExecutionContext:
    return ExecutionContext(
        cwd=str(root),
        workspace_roots=(str(root),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _prepare(tool: MoveTool, root: Path, source: str, target: str):  # type: ignore[no-untyped-def]
    return tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-move",
            tool_name="fs.move",
            arguments={"source": source, "target": target},
            tool_call_id="call-move",
        ),
        _context(root),
    )


def test_move_requires_an_existing_target_parent(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    error = _prepare(MoveTool(lambda _a, _b: None), tmp_path, "a.txt", "new/a.txt")

    assert isinstance(error, PreparationError)
    assert "父目录" in error.message


def test_move_rejects_symlink_sources(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(tmp_path / "a.txt")

    error = _prepare(MoveTool(lambda _a, _b: None), tmp_path, "link.txt", "moved.txt")

    assert isinstance(error, PreparationError)
    assert "符号链接" in error.message


def test_move_refuses_to_run_when_source_changed_after_prepare(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("before", encoding="utf-8")
    moves: list[tuple[str, str]] = []
    tool = MoveTool(lambda left, right: moves.append((left, right)))
    plan = _prepare(tool, tmp_path, "a.txt", "b.txt")
    assert isinstance(plan, ToolPlan)
    source.write_text("after", encoding="utf-8")

    result = tool.perform(plan, _context(tmp_path))

    assert result.status is ToolResultStatus.TOOL_ERROR
    assert result.error is not None and result.error.code == "target_changed"
    assert moves == []
