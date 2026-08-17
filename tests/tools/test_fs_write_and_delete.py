"""fs.write_patch 的精确替换与 fs.delete 的目录支持.

两件事都在 prepare 阶段定死: 替换后的内容与要删的文件清单都是**计划里的事实**, 不是
执行时才算的. 裁决, 审批与恢复层拿到的因此是"文件会变成什么样", 而不是一段还要再解释
一次的意图.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.builtin import DeleteTool, WritePatchTool
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import TargetResolution, ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _context(workspace: Path) -> ExecutionContext:
    return ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _prepare(tool: object, workspace: Path, **arguments: object):  # type: ignore[no-untyped-def]
    return tool.prepare(  # type: ignore[attr-defined]
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="t",
            arguments=arguments,
            tool_call_id="c1",
        ),
        _context(workspace),
    )


# ---- fs.write_patch ----


def _write_tool() -> WritePatchTool:
    return WritePatchTool(lambda path, content: Path(path).write_text(content, "utf-8"))


def test_a_unique_fragment_is_replaced(workspace: Path) -> None:
    target = workspace / "a.py"
    target.write_text("x = 1\ny = 2\n", encoding="utf-8")

    plan = _prepare(
        _write_tool(), workspace, path="a.py", old_string="y = 2", new_string="y = 3"
    )

    assert isinstance(plan, ToolPlan)
    assert plan.normalized_input["content"] == "x = 1\ny = 3\n"


def test_an_ambiguous_fragment_is_rejected_with_the_count(workspace: Path) -> None:
    """命中多处时不猜: 改错了比没改更难发现."""
    target = workspace / "a.py"
    target.write_text("v = 0\nv = 0\n", encoding="utf-8")

    error = _prepare(
        _write_tool(), workspace, path="a.py", old_string="v = 0", new_string="v = 1"
    )

    assert isinstance(error, PreparationError)
    assert "2 次" in error.message
    assert "replace_all" in error.message


def test_replace_all_makes_multiple_occurrences_explicit(workspace: Path) -> None:
    target = workspace / "a.py"
    target.write_text("v = 0\nv = 0\n", encoding="utf-8")

    plan = _prepare(
        _write_tool(),
        workspace,
        path="a.py",
        old_string="v = 0",
        new_string="v = 1",
        replace_all=True,
    )

    assert isinstance(plan, ToolPlan)
    assert plan.normalized_input["content"] == "v = 1\nv = 1\n"


def test_a_fragment_that_does_not_exist_is_rejected(workspace: Path) -> None:
    (workspace / "a.py").write_text("x = 1\n", encoding="utf-8")

    error = _prepare(
        _write_tool(), workspace, path="a.py", old_string="缺失", new_string="x"
    )

    assert isinstance(error, PreparationError)
    assert "逐字符一致" in error.message


def test_an_empty_old_string_creates_a_new_file(workspace: Path) -> None:
    plan = _prepare(
        _write_tool(), workspace, path="new.py", old_string="", new_string="print(1)\n"
    )

    assert isinstance(plan, ToolPlan)
    assert plan.normalized_input["content"] == "print(1)\n"


def test_an_empty_old_string_cannot_overwrite_an_existing_file(workspace: Path) -> None:
    """这条挡的是"全文覆盖"的后门 —— 而全文覆盖正是这次要去掉的东西."""
    (workspace / "a.py").write_text("重要内容\n", encoding="utf-8")

    error = _prepare(
        _write_tool(), workspace, path="a.py", old_string="", new_string="没了"
    )

    assert isinstance(error, PreparationError)
    assert "已存在" in error.message


def test_editing_a_missing_file_points_at_the_right_fix(workspace: Path) -> None:
    error = _prepare(
        _write_tool(), workspace, path="nope.py", old_string="a", new_string="b"
    )

    assert isinstance(error, PreparationError)
    assert "old_string 留空" in error.message


def test_the_plan_declares_the_write_target(workspace: Path) -> None:
    (workspace / "a.py").write_text("x\n", encoding="utf-8")
    plan = _prepare(
        _write_tool(), workspace, path="a.py", old_string="x", new_string="y"
    )
    assert isinstance(plan, ToolPlan)
    assert plan.effects.write_paths == (str((workspace / "a.py").resolve()),)


# ---- fs.delete ----


def _delete_tool(removed: list[str]) -> DeleteTool:
    return DeleteTool(removed.append)


def test_deleting_a_file_targets_that_file(workspace: Path) -> None:
    (workspace / "a.py").write_text("x\n", encoding="utf-8")

    plan = _prepare(_delete_tool([]), workspace, path="a.py")

    assert isinstance(plan, ToolPlan)
    assert plan.effects.delete_paths == (str((workspace / "a.py").resolve()),)
    assert plan.target_resolution is TargetResolution.STATIC


def test_a_non_empty_directory_needs_an_explicit_recursive_flag(
    workspace: Path,
) -> None:
    """回归: fs.delete 以前根本删不掉目录 (unlink 对目录抛 IsADirectoryError)."""
    tree = workspace / "pkg"
    tree.mkdir()
    (tree / "a.py").write_text("x\n", encoding="utf-8")

    error = _prepare(_delete_tool([]), workspace, path="pkg")

    assert isinstance(error, PreparationError)
    assert "recursive=true" in error.message
    assert "1 个文件" in error.message


def test_a_recursive_delete_expands_to_every_file(workspace: Path) -> None:
    tree = workspace / "pkg"
    (tree / "sub").mkdir(parents=True)
    (tree / "a.py").write_text("x\n", encoding="utf-8")
    (tree / "sub" / "b.py").write_text("y\n", encoding="utf-8")

    plan = _prepare(_delete_tool([]), workspace, path="pkg", recursive=True)

    assert isinstance(plan, ToolPlan)
    # 目标是逐个文件而不是一个目录名: 恢复层按路径存 preimage, 审批界面按路径列清单.
    assert plan.effects.delete_paths == (
        str((tree / "a.py").resolve()),
        str((tree / "sub" / "b.py").resolve()),
    )
    assert plan.target_resolution is TargetResolution.FORGE_EXPANDED


def test_the_delete_spec_declares_what_prepare_actually_produces(
    workspace: Path,
) -> None:
    """回归: spec 声明 STATIC 而目录删除产出 FORGE_EXPANDED, 协调器会 fail closed.

    这条单测直接钉 spec 与 prepare 的一致性, 因为其余 fs.delete 用例都绕过
    ToolRequestCoordinator 直接调 prepare —— 于是声明不匹配在单测里完全看不见,
    只有真的去删一个目录时才炸 (ADR-0004 §4, 协调器 _check_declaration).
    """
    tree = workspace / "pkg"
    tree.mkdir()
    (tree / "a.py").write_text("x\n", encoding="utf-8")

    tool = _delete_tool([])
    plan = _prepare(tool, workspace, path="pkg", recursive=True)

    assert isinstance(plan, ToolPlan)
    assert tool.spec.target_declaration_ability.permits(plan.target_resolution)


def test_an_empty_directory_needs_no_flag(workspace: Path) -> None:
    (workspace / "empty").mkdir()
    plan = _prepare(_delete_tool([]), workspace, path="empty")
    assert isinstance(plan, ToolPlan)


def test_deleting_a_missing_path_is_rejected(workspace: Path) -> None:
    error = _prepare(_delete_tool([]), workspace, path="nope")
    assert isinstance(error, PreparationError)
    assert "不存在" in error.message
