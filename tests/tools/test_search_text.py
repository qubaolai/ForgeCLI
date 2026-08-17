"""search.text 的正则, 上下文与分组输出.

这个工具做好了能直接顶掉多次 fs.read_file —— 一次搜索带上前后文, 模型往往就不用再把
整个文件读进来. 所以它的输出质量与"重复调用"是同一个问题.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.artifact_store import NullArtifactStore
from forgecli.application.tools.builtin import SearchTextTool
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE

SOURCE = """def login(user):
    if not user:
        raise ValueError("no user")
    return authenticate(user)


def logout(user):
    return drop_session(user)
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "auth.py").write_text(SOURCE, encoding="utf-8")
    return root


def _run(workspace: Path, **arguments: object) -> str:
    tool = SearchTextTool(ResourceGovernor(), NullArtifactStore())
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
            tool_name="search.text",
            arguments=arguments,
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    return tool.perform(plan, context).text


def test_a_substring_hit_is_grouped_under_its_file(workspace: Path) -> None:
    output = _run(workspace, query="def login")
    assert "auth.py  (1 处)" in output
    assert "1:def login(user):" in output


def test_regex_matches(workspace: Path) -> None:
    output = _run(workspace, query=r"^def \w+", regex=True)
    assert "(2 处)" in output


def test_an_illegal_regex_is_rejected_at_prepare(workspace: Path) -> None:
    tool = SearchTextTool(ResourceGovernor(), NullArtifactStore())
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    error = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="search.text",
            arguments={"query": "([unclosed", "regex": True},
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(error, PreparationError)
    assert "合法正则" in error.message


def test_context_lines_bring_the_surrounding_code(workspace: Path) -> None:
    output = _run(workspace, query="raise ValueError", context_lines=2)
    assert "2-    if not user:" in output
    assert "3:        raise ValueError" in output
    assert "4-    return authenticate(user)" in output


def test_separate_hits_are_divided(workspace: Path) -> None:
    output = _run(workspace, query="user", context_lines=0)
    assert "  --" in output, "不相邻的命中之间要有分隔, 否则读起来像连续的代码"


def test_an_empty_result_says_how_many_files_were_scanned(workspace: Path) -> None:
    """只回"未找到"时模型分不清是确实没有还是搜错地方, 于是换个写法再试一次."""
    output = _run(workspace, query="绝不存在的词")
    assert "扫描了 1 个文件" in output
    assert "确定的空结果" in output


def test_a_pattern_matching_nothing_blames_the_pattern(workspace: Path) -> None:
    output = _run(workspace, query="login", pattern="**/*.rs")
    assert "没有文件被扫描" in output
    assert "不是搜索词" in output
