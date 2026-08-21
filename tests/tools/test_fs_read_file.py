"""fs.read_file 的按行取段.

大文件整篇读回来会把上下文吃光, 所以要能只取一段. 但"只取一段"有个隐藏代价: 模型拿到
的是一段没有坐标的文本, 它无从判断上面还有没有内容, 于是把"这一段里没有"当成"这个文件
里没有". 位置说明就是为这个而存在的, 而且必须单独成一个 part —— 拼进正文的话, 模型
照抄一段当 old_string 时会把它一起抄走, fs.edit_file 的逐字比对必然对不上.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.artifact_store import NullArtifactStore
from forgecli.application.tools.builtin import ReadFileTool
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult, ToolResultStatus
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.py").write_text("".join(f"line {n}\n" for n in range(1, 11)), "utf-8")
    return root


def _read(workspace: Path, **arguments: object) -> ToolResult:
    tool = ReadFileTool(ResourceGovernor(), NullArtifactStore())
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
            tool_name="fs.read_file",
            arguments={"path": "a.py", **arguments},
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan)
    return tool.perform(plan, context)


def test_reading_without_a_window_returns_the_file_verbatim(workspace: Path) -> None:
    result = _read(workspace)

    assert result.text == (workspace / "a.py").read_text(encoding="utf-8")
    # 没开窗就没有位置说明: 全文本来就不需要坐标.
    assert len(result.content_parts) == 1


def test_a_window_returns_only_those_lines(workspace: Path) -> None:
    result = _read(workspace, offset=3, limit=2)

    assert result.content_parts[0].text == "line 3\nline 4\n"


def test_a_window_states_where_it_sits_in_the_whole_file(workspace: Path) -> None:
    result = _read(workspace, offset=3, limit=2)

    assert result.content_parts[-1].text == "[以上是第 3-4 行, 全文共 10 行]"


def test_the_position_note_is_a_separate_part_from_the_content(
    workspace: Path,
) -> None:
    """回归: 拼进正文的话, 模型照抄这段当 old_string 会把说明一起抄走."""
    result = _read(workspace, offset=1, limit=1)

    assert "全文共" not in result.content_parts[0].text
    assert len(result.content_parts) == 2


def test_an_offset_past_the_end_says_so_instead_of_returning_nothing(
    workspace: Path,
) -> None:
    """空回答与"读到头了"长得一样, 模型只会换个 offset 再试一次."""
    result = _read(workspace, offset=99)

    assert result.content_parts[-1].text == "[第 99 行超出文件末尾, 全文共 10 行]"


def test_offset_alone_reads_to_the_end(workspace: Path) -> None:
    result = _read(workspace, offset=9)

    assert result.content_parts[0].text == "line 9\nline 10\n"


def test_a_byte_limited_read_explicitly_says_the_artifact_is_incomplete(
    workspace: Path,
) -> None:
    result = _read(workspace, max_bytes=12)

    assert "内容不完整" in result.text
    assert "artifact" in result.text


def test_offset_beyond_a_truncated_prefix_does_not_claim_end_of_file(
    workspace: Path,
) -> None:
    result = _read(workspace, max_bytes=12, offset=9)

    assert "已读取前缀" in result.text
    assert "不能据此判断全文末尾" in result.text


def test_read_refuses_a_file_that_changed_after_prepare(workspace: Path) -> None:
    tool = ReadFileTool(ResourceGovernor(), NullArtifactStore())
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-changed",
            tool_name="fs.read_file",
            arguments={"path": "a.py"},
            tool_call_id="c-changed",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan)
    (workspace / "a.py").write_text("changed\n", encoding="utf-8")

    result = tool.perform(plan, context)

    assert result.status is ToolResultStatus.TOOL_ERROR
    assert result.error is not None and result.error.code == "target_changed"
