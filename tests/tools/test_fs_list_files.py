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
