"""fs.create_file 的新建, fs.edit_file 的精确替换, fs.delete 的目录支持.

三件事都在 prepare 阶段定死: 要写的内容与要删的文件清单都是**计划里的事实**, 不是
执行时才算的. 裁决, 审批与恢复层拿到的因此是"文件会变成什么样", 而不是一段还要再解释
一次的意图.

新建与修改分成两个工具之后, 各自的前置条件方向相反 (create 要求不存在, edit 要求存在),
所以两边的"走错门"用例都要钉住: 错误信息必须指向另一个工具, 否则模型只会原地重试.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.builtin import (
    CreateFileTool,
    DeleteTool,
    EditFileTool,
)
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


def _writer(path: str, content: str) -> None:
    Path(path).write_text(content, "utf-8")


# ---- fs.create_file ----


def test_creating_a_file_carries_the_whole_content(workspace: Path) -> None:
    plan = _prepare(
        CreateFileTool(_writer), workspace, path="new.py", content="print(1)\n"
    )

    assert isinstance(plan, ToolPlan)
    assert plan.normalized_input["content"] == "print(1)\n"
    assert plan.effects.write_paths == (str(workspace / "new.py"),)


def test_creating_over_an_existing_file_is_refused(workspace: Path) -> None:
    """挡的是"全文覆盖"的后门: 模型没读全文件就能把它整段换掉, 丢掉的部分没人看得见."""
    (workspace / "a.py").write_text("重要内容\n", encoding="utf-8")

    error = _prepare(CreateFileTool(_writer), workspace, path="a.py", content="没了")

    assert isinstance(error, PreparationError)
    assert "已存在" in error.message
    assert "fs.edit_file" in error.message


def test_the_create_plan_shows_what_the_file_will_contain(workspace: Path) -> None:
    """审批界面靠 content_previews 逐字展示, 少了它用户只能看到一个路径就点批准."""
    plan = _prepare(CreateFileTool(_writer), workspace, path="a.py", content="x = 1\n")

    assert isinstance(plan, ToolPlan)
    assert [preview.content for preview in plan.content_previews] == ["x = 1\n"]


# ---- fs.edit_file ----


def _write_tool() -> EditFileTool:
    return EditFileTool(_writer)


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


def test_an_empty_old_string_points_at_the_create_tool(workspace: Path) -> None:
    """回归: 新建文件曾经靠"old_string 传空串"表达, 一个没写在名字上的隐藏约定.

    模型找不到叫"建文件"的工具, 于是编了个 fs.write_file 调过去, 拿回"未注册",
    转头改用 shell 写文件 —— 绕开了整条恢复与裁决链路.
    """
    error = _prepare(
        _write_tool(), workspace, path="new.py", old_string="", new_string="print(1)\n"
    )

    assert isinstance(error, PreparationError)
    assert "fs.create_file" in error.message


def test_editing_a_missing_file_points_at_the_create_tool(workspace: Path) -> None:
    error = _prepare(
        _write_tool(), workspace, path="nope.py", old_string="a", new_string="b"
    )

    assert isinstance(error, PreparationError)
    assert "fs.create_file" in error.message


def test_a_replacement_that_changes_nothing_is_refused(workspace: Path) -> None:
    """放行等于消耗一次审批写回一模一样的内容, 而模型会把"成功"当成"改动生效了"."""
    (workspace / "a.py").write_text("x = 1\n", encoding="utf-8")

    error = _prepare(
        _write_tool(), workspace, path="a.py", old_string="x = 1", new_string="x = 1"
    )

    assert isinstance(error, PreparationError)
    assert "不会改变任何内容" in error.message


def test_a_whitespace_only_mismatch_says_so(workspace: Path) -> None:
    """只说"找不到", 模型唯一能做的是换个写法再试 —— 而它看不出差的是缩进."""
    (workspace / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    error = _prepare(
        _write_tool(),
        workspace,
        path="a.py",
        old_string="def f():\n        return 1",
        new_string="def f():\n    return 2",
    )

    assert isinstance(error, PreparationError)
    assert "空白" in error.message


def test_a_missing_fragment_reports_where_its_first_line_appears(
    workspace: Path,
) -> None:
    (workspace / "a.py").write_text("a\nvalue = 1\nb\n", encoding="utf-8")

    error = _prepare(
        _write_tool(),
        workspace,
        path="a.py",
        old_string="value = 1  # 注释",
        new_string="value = 2",
    )

    assert isinstance(error, PreparationError)
    assert "第 2" in error.message


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
