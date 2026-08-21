"""fs.list_files 的深度限制与默认忽略规则.

这个工具的输出质量与"重复调用"直接相关: 一次 `**/*` 吐出几万条 node_modules 路径,
模型看到的全是噪音, 只好换个 pattern 再列一次.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.artifact_store import NullArtifactStore
from forgecli.application.tools.builtin import ListFilesTool
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.plan import ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE


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
    tool = ListFilesTool(ResourceGovernor(), NullArtifactStore())
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
            tool_name="fs.list_files",
            arguments={"pattern": "**/*", **arguments},
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
    tool = ListFilesTool(ResourceGovernor(), NullArtifactStore())
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
            tool_name="fs.list_files",
            arguments={"pattern": "**/*"},
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan)
    assert not [path for path in plan.effects.read_paths if "node_modules" in path]


# ---- description 承诺的两条写法 ----
#
# description 是模型唯一能看到的工具说明. 它写下的每个例子都是承诺, 承诺失效时没有任何
# 东西会报错 —— 提示词照常渲染, 模型照常按它去调, 只是拿不到东西, 然后退回 shell.run.


def test_the_default_pattern_lists_only_one_level(workspace: Path) -> None:
    """默认 '*' 只列当前一层. 这条要钉住, 因为它是逐层遍历的成因.

    与 search.text 相反 (那个默认 '**/*' 递归), 两个默认值不一致本身就是陷阱, 所以
    两边的 description 都必须写明自己的默认值.
    """
    entries = _entries(workspace, pattern="*")
    assert "a.py" in entries
    assert "src" in entries
    assert "src/b.py" not in entries


def test_a_suffix_glob_reaches_the_whole_tree(workspace: Path) -> None:
    """description 里 '**/*.java' 那个例子的等价形式. 一次调用拿到整棵树的同类文件."""
    entries = _entries(workspace, pattern="**/*.py")
    assert entries == ["a.py", "src/b.py", "src/deep/c.py", "src/deep/deeper/d.py"]


def test_a_name_glob_finds_files_by_name(workspace: Path) -> None:
    """description 里 '**/application*.yml' 那个例子的等价形式.

    这是全仓找文件名的唯一一条专用工具通道; 它不成立的话, 模型只剩 shell.run 的 find.
    """
    entries = _entries(workspace, pattern="**/d*.py")
    assert entries == ["src/deep/deeper/d.py"]


def test_entry_limit_is_reported_instead_of_silently_truncating(
    workspace: Path,
) -> None:
    bulk = workspace / "bulk"
    bulk.mkdir()
    for index in range(2001):
        (bulk / f"{index:04}.txt").touch()
    tool = ListFilesTool(ResourceGovernor(), NullArtifactStore())
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
            tool_name="fs.list_files",
            arguments={"path": "bulk", "pattern": "*"},
            tool_call_id="c-limit",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan)

    result = tool.perform(plan, context)

    assert "结果不完整" in result.text
    assert "2000" in result.text
