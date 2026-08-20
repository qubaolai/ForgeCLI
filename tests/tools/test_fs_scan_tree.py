"""fs.scan_tree: 认识一个陌生目录的第一步.

这个工具存在的理由是 fs.list_files 回答不了"这个仓库长什么样". 用 `**/*` 硬凑, 拿回的
是几千行绝对路径, 每行重复同一个前缀 —— 上下文被冲光, 而问题仍然没答案. 所以这里钉的
不是"能不能列出文件", 而是**形状**: 有层级, 深度到头要说清还有多少没展开, 超预算要说
自己不完整.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.artifact_store import NullArtifactStore
from forgecli.application.tools.builtin import ScanTreeTool
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import TargetResolution, ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (root / "src" / "pkg" / "deep.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests" / "test_main.py").write_text("assert 1\n", encoding="utf-8")
    (root / "node_modules" / "left-pad" / "index.js").write_text("//", encoding="utf-8")
    return root


def _context(workspace: Path) -> ExecutionContext:
    return ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _tool() -> ScanTreeTool:
    return ScanTreeTool(ResourceGovernor(), NullArtifactStore())


def _run(workspace: Path, **arguments: object) -> str:
    tool = _tool()
    context = _context(workspace)
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="fs.scan_tree",
            arguments=arguments,
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan)
    return tool.perform(plan, context).text


def _prepare(workspace: Path, **arguments: object) -> ToolPlan | PreparationError:
    return _tool().prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="fs.scan_tree",
            arguments=arguments,
            tool_call_id="c1",
        ),
        _context(workspace),
    )


def test_the_tree_is_indented_and_directories_come_first(workspace: Path) -> None:
    text = _run(workspace)
    lines = [line for line in text.splitlines() if line.strip()]

    # 第一行是根与规模, 之后才是内容.
    assert lines[0].startswith(str(workspace))
    body = lines[1:]
    assert body[0] == "src/"
    assert "  main.py  9B" in body
    assert "    deep.py  6B" in body
    # 目录在前, 文件在后: README.md 是根下唯一的文件, 排在两个目录之后.
    assert body.index("tests/") < body.index("README.md  7B")


def test_generated_directories_stay_out_unless_asked_for(workspace: Path) -> None:
    """一次 node_modules 就能把整棵树淹掉, 而模型几乎从不需要读它."""
    assert "node_modules/" not in _run(workspace)
    assert "node_modules/" in _run(workspace, include_ignored=True)


def test_a_directory_beyond_the_depth_reports_how_much_is_left(
    workspace: Path,
) -> None:
    """ "空目录"和"没展开"长得完全不同, 混起来模型会以为这条路走到头了."""
    text = _run(workspace, depth=1)

    assert "src/  (2 项未展开)" in text
    assert "main.py" not in text


def test_an_empty_directory_says_it_is_empty(workspace: Path) -> None:
    (workspace / "empty").mkdir()
    assert "empty/  (空)" in _run(workspace, depth=1)


def test_hitting_the_entry_budget_is_stated_not_hidden(workspace: Path) -> None:
    """悄悄截断的话, 模型会把这份残缺的树当成完整结构去推断."""
    text = _run(workspace, max_entries=2)

    assert "结构不完整" in text
    assert "更小的 path" in text


def test_the_scanned_paths_are_frozen_into_the_plan(workspace: Path) -> None:
    """目标集合在 prepare 就封闭, 声明为 FORGE_EXPANDED (ADR-0004 §4)."""
    plan = _prepare(workspace)

    assert isinstance(plan, ToolPlan)
    assert plan.target_resolution is TargetResolution.FORGE_EXPANDED
    assert str(workspace / "src" / "main.py") in plan.effects.read_paths
    assert _tool().spec.target_declaration_ability.permits(plan.target_resolution)


def test_the_same_directory_scans_to_the_same_tree_twice(workspace: Path) -> None:
    """排序不是审美: 目录项返回顺序一变, plan_hash 就变, 上一次批准也就绑不住这一次."""
    first = _prepare(workspace)
    second = _prepare(workspace)

    assert isinstance(first, ToolPlan)
    assert isinstance(second, ToolPlan)
    assert first.plan_hash == second.plan_hash


def test_pointing_at_a_file_says_which_tool_to_use(workspace: Path) -> None:
    error = _prepare(workspace, path="README.md")

    assert isinstance(error, PreparationError)
    assert "fs.read_file" in error.message
