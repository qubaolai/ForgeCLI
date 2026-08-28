"""fs_find 的两种形态: 不给 name_glob 走目录树, 给了 name_glob 走扁平清单.

合并自原先的 `test_fs_list_files.py` 与 `test_fs_scan_tree.py` —— 那两个工具是同一个
动作的两个入口 (ADR-0029 A 类), 合并之后**行为一条不减**, 只是入口从两个变成一个,
输出形态由 name_glob 在不在决定.

输出质量与"重复调用"直接相关: 一次 `**/*` 吐出几万条 node_modules 路径, 模型看到的
全是噪音, 只好换个 name_glob 再列一次.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.builtin.fs_find import FindTool
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import TargetResolution, ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE, NullArtifactStore


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "src" / "deep" / "deeper").mkdir(parents=True)
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    (root / "a.py").write_text("x", encoding="utf-8")
    (root / "src" / "b.py").write_text("x", encoding="utf-8")
    (root / "src" / "deep" / "c.py").write_text("x", encoding="utf-8")
    (root / "src" / "deep" / "deeper" / "d.py").write_text("x", encoding="utf-8")
    (root / "node_modules" / "left-pad" / "index.js").write_text("x", encoding="utf-8")
    (root / "__pycache__" / "a.pyc").write_text("x", encoding="utf-8")
    return root


def _entries(workspace: Path, **arguments: object) -> list[str]:
    tool = FindTool(ResourceGovernor(), NullArtifactStore())
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="fs_find",
            arguments={"name_glob": "**/*", **arguments},
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    root = str(workspace.resolve()) + "/"
    return sorted(
        str(item).removeprefix(root) for item in plan.normalized_input["entries"]
    )


def test_generated_directories_are_ignored_by_default(workspace: Path) -> None:
    listed = _entries(workspace)
    assert "a.py" in listed
    assert not [item for item in listed if "node_modules" in item]
    assert not [item for item in listed if "__pycache__" in item]


def test_include_ignored_brings_them_back(workspace: Path) -> None:
    listed = _entries(workspace, include_ignored=True)
    assert [item for item in listed if "node_modules" in item]


def test_depth_limits_how_deep_the_listing_goes(workspace: Path) -> None:
    assert _entries(workspace, depth=1) == ["a.py", "src"]
    listed = _entries(workspace, depth=2)
    assert "src/b.py" in listed
    assert "src/deep/c.py" not in listed


def test_without_depth_the_whole_tree_is_listed(workspace: Path) -> None:
    assert "src/deep/deeper/d.py" in _entries(workspace)


def test_the_filtered_paths_never_reach_the_plan_targets(workspace: Path) -> None:
    """被忽略的路径不该出现在 read_paths 里 —— 安全侧不必为压根不读的文件做判断."""
    tool = FindTool(ResourceGovernor(), NullArtifactStore())
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="fs_find",
            arguments={"name_glob": "**/*"},
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan)
    assert not [path for path in plan.effects.read_paths if "node_modules" in path]


# ---- description 承诺的两条写法 ----
#
# description 是模型唯一能看到的工具说明. 它写下的每个例子都是承诺, 承诺失效时没有任何
# 东西会报错 —— 提示词照常渲染, 模型照常按它去调, 只是拿不到东西, 然后退回 shell_run.


def test_a_single_star_lists_only_one_level(workspace: Path) -> None:
    """默认 '*' 只列当前一层. 这条要钉住, 因为它是逐层遍历的成因.

    与 search_text 相反 (那个默认 '**/*' 递归), 两个默认值不一致本身就是陷阱, 所以
    两边的 description 都必须写明自己的默认值.
    """
    entries = _entries(workspace, name_glob="*")
    assert "a.py" in entries
    assert "src" in entries
    assert "src/b.py" not in entries


def test_a_suffix_glob_reaches_the_whole_tree(workspace: Path) -> None:
    """description 里 '**/*.java' 那个例子的等价形式. 一次调用拿到整棵树的同类文件."""
    entries = _entries(workspace, name_glob="**/*.py")
    assert entries == ["a.py", "src/b.py", "src/deep/c.py", "src/deep/deeper/d.py"]


def test_a_name_glob_finds_files_by_name(workspace: Path) -> None:
    """description 里 '**/application*.yml' 那个例子的等价形式.

    这是全仓找文件名的唯一一条专用工具通道; 它不成立的话, 模型只剩 shell_run 的 find.
    """
    entries = _entries(workspace, name_glob="**/d*.py")
    assert entries == ["src/deep/deeper/d.py"]


def test_entry_limit_is_reported_instead_of_silently_truncating(
    workspace: Path,
) -> None:
    bulk = workspace / "bulk"
    bulk.mkdir()
    for index in range(2001):
        (bulk / f"{index:04}.txt").touch()
    tool = FindTool(ResourceGovernor(), NullArtifactStore())
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-limit",
            tool_name="fs_find",
            arguments={"path": "bulk", "name_glob": "*"},
            tool_call_id="c-limit",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan)

    result = tool.perform(plan, context)

    assert "结果不完整" in result.text
    assert "2000" in result.text


@pytest.fixture
def tree_workspace(tmp_path: Path) -> Path:
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


def _context(tree_workspace: Path) -> ExecutionContext:
    return ExecutionContext(
        cwd=str(tree_workspace),
        workspace_roots=(str(tree_workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _tool() -> FindTool:
    return FindTool(ResourceGovernor(), NullArtifactStore())


def _run(tree_workspace: Path, **arguments: object) -> str:
    tool = _tool()
    context = _context(tree_workspace)
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="fs_find",
            arguments=arguments,
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan)
    return tool.perform(plan, context).text


def _prepare(tree_workspace: Path, **arguments: object) -> ToolPlan | PreparationError:
    return _tool().prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="fs_find",
            arguments=arguments,
            tool_call_id="c1",
        ),
        _context(tree_workspace),
    )


def test_the_tree_is_indented_and_directories_come_first(tree_workspace: Path) -> None:
    text = _run(tree_workspace)
    lines = [line for line in text.splitlines() if line.strip()]

    # 第一行是根与规模, 之后才是内容.
    assert lines[0].startswith(str(tree_workspace))
    body = lines[1:]
    assert body[0] == "src/"
    assert "  main.py  9B" in body
    assert "    deep.py  6B" in body
    # 目录在前, 文件在后: README.md 是根下唯一的文件, 排在两个目录之后.
    assert body.index("tests/") < body.index("README.md  7B")


def test_generated_directories_stay_out_unless_asked_for(tree_workspace: Path) -> None:
    """一次 node_modules 就能把整棵树淹掉, 而模型几乎从不需要读它."""
    assert "node_modules/" not in _run(tree_workspace)
    assert "node_modules/" in _run(tree_workspace, include_ignored=True)


def test_a_directory_beyond_the_depth_reports_how_much_is_left(
    tree_workspace: Path,
) -> None:
    """ "空目录"和"没展开"长得完全不同, 混起来模型会以为这条路走到头了."""
    text = _run(tree_workspace, depth=1)

    assert "src/  (2 项未展开)" in text
    assert "main.py" not in text


def test_an_empty_directory_says_it_is_empty(tree_workspace: Path) -> None:
    (tree_workspace / "empty").mkdir()
    assert "empty/  (空)" in _run(tree_workspace, depth=1)


def test_hitting_the_entry_budget_is_stated_not_hidden(tree_workspace: Path) -> None:
    """悄悄截断的话, 模型会把这份残缺的树当成完整结构去推断."""
    text = _run(tree_workspace, max_entries=2)

    assert "结构不完整" in text
    assert "更小的 path" in text


def test_the_scanned_paths_are_frozen_into_the_plan(tree_workspace: Path) -> None:
    """目标集合在 prepare 就封闭, 声明为 FORGE_EXPANDED (ADR-0004 §4)."""
    plan = _prepare(tree_workspace)

    assert isinstance(plan, ToolPlan)
    assert plan.target_resolution is TargetResolution.FORGE_EXPANDED
    assert str(tree_workspace / "src" / "main.py") in plan.effects.read_paths
    assert _tool().spec.target_declaration_ability.permits(plan.target_resolution)


def test_the_same_directory_scans_to_the_same_tree_twice(tree_workspace: Path) -> None:
    """排序不是审美: 目录项返回顺序一变, plan_hash 就变, 上一次批准也就绑不住这一次."""
    first = _prepare(tree_workspace)
    second = _prepare(tree_workspace)

    assert isinstance(first, ToolPlan)
    assert isinstance(second, ToolPlan)
    assert first.plan_hash == second.plan_hash


def test_pointing_at_a_file_says_which_tool_to_use(tree_workspace: Path) -> None:
    error = _prepare(tree_workspace, path="README.md")

    assert isinstance(error, PreparationError)
    assert "fs_read" in error.message
