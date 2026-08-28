"""code_definitions: 按符号名找定义 (tree-sitter).

这里钉的是它与 search_text 的**分工**: 只出定义, 不出 import / 注释 / 调用点. 那条分工
是这个工具存在的全部理由 —— 分不开的话它就只是一个更慢的 search_text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.builtin.code_definitions import CodeDefinitionsTool
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE, NullArtifactStore

_PYTHON = '''\
"""模块注释里也提到 UsageMeter, 但那不是定义."""

from forgecli.metering import UsageMeter


class UsageRecordDraft:
    pass


class UsageMeter:
    """真正的定义在这里."""

    def record(self) -> None:
        # UsageMeter 出现在注释里
        meter = UsageMeter()
        return meter.noop()
'''

_JAVA = """\
package com.example;

public final class DataSourceConfig {
    public static void setCurrent(String name) {}
}
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "metering.py").write_text(_PYTHON, encoding="utf-8")
    (tmp_path / "DataSourceConfig.java").write_text(_JAVA, encoding="utf-8")
    (tmp_path / "notes.md").write_text("UsageMeter 在文档里也出现\n", encoding="utf-8")
    # 认不出语言的文件. 它不该进目标集合 —— 我们压根不打算解析它.
    (tmp_path / "blob.dat").write_bytes(b"\x00\x01UsageMeter\x02")
    return tmp_path


def _context(workspace: Path) -> ExecutionContext:
    return ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _run(workspace: Path, **arguments: object) -> str:
    tool = CodeDefinitionsTool(ResourceGovernor(), NullArtifactStore())
    context = _context(workspace)
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-defs",
            tool_name="code_definitions",
            arguments=arguments,
            tool_call_id="call-defs",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    result = tool.perform(plan, context)
    return "\n".join(part.text for part in result.content_parts if part.text)


def test_it_finds_the_definition_and_not_the_import_or_the_comment(
    workspace: Path,
) -> None:
    """同一个文件里 UsageMeter 出现 5 次, 只有一次是定义.

    search_text 在这里会给出 5 行, 模型要自己分辨哪一行才是定义 —— 而它分辨的方式是
    再读一遍文件. 这条用例钉住这个工具只给那一行.
    """
    output = _run(workspace, symbol="UsageMeter")

    assert "metering.py" in output
    # 定义那一行, 且只有那一行: import (第 3 行) 与注释里的用法都不在.
    assert "10: class UsageMeter" in output
    assert output.count("UsageMeter") == 1
    assert "notes.md" not in output


def test_the_line_number_is_one_based_like_the_other_tools(workspace: Path) -> None:
    """tree-sitter 的 start_line 从 0 起. 行号与 fs_read / search_text 不一致的话,
    模型拿着它去 fs_read(offset=N) 会差一行, 而差一行读出来的东西看着也说得通.
    """
    output = _run(workspace, symbol="UsageMeter")

    # _PYTHON 里 `class UsageMeter:` 是第 10 行 (从 1 起).
    assert "10:" in output


def test_it_crosses_languages(workspace: Path) -> None:
    """Java 的 `public final class X` 用 `class X` 做文本匹配是搜不到的."""
    output = _run(workspace, symbol="DataSourceConfig")

    assert "DataSourceConfig.java" in output


def test_kind_narrows_the_result(workspace: Path) -> None:
    assert "class UsageRecordDraft" in _run(workspace, symbol="UsageRecordDraft")
    # 它是个类, 按 function 筛就该落空 —— 落空时回的是空结果说明, 里面会重复符号名,
    # 所以查的是"有没有那一行定义", 不是"正文里有没有这个词".
    assert "class UsageRecordDraft" not in _run(
        workspace, symbol="UsageRecordDraft", kind="function"
    )


def test_prefix_matching_lists_a_family(workspace: Path) -> None:
    output = _run(workspace, symbol="Usage", prefix=True)

    assert "UsageMeter" in output
    assert "UsageRecordDraft" in output


def test_a_miss_says_how_many_files_were_parsed_and_points_at_search_text(
    workspace: Path,
) -> None:
    """空结果要能让模型分辨"确实没有"和"找错地方了".

    只回"未找到"时它会换个写法反复重试 —— 那正是 search_text 的 _empty_message 当初要
    解决的问题, 这里同样不能省. 多一句"可能来自依赖库": 一个符号定义不在工作区里是常态,
    而模型读不出这一点就会继续扩大 path 再找一遍.
    """
    output = _run(workspace, symbol="NotDefinedAnywhere")

    assert "解析了 3 个源文件" in output
    assert "依赖库" in output
    assert "search_text" in output


def test_a_directory_without_source_files_says_the_path_is_the_problem(
    tmp_path: Path,
) -> None:
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")

    output = _run(tmp_path, symbol="X")

    assert "没有文件被解析" in output
    assert "问题出在 path 上" in output


def test_an_empty_symbol_is_rejected(workspace: Path) -> None:
    tool = CodeDefinitionsTool(ResourceGovernor(), NullArtifactStore())
    error = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-empty",
            tool_name="code_definitions",
            arguments={"symbol": "   "},
            tool_call_id="call-empty",
        ),
        _context(workspace),
    )

    assert isinstance(error, PreparationError)


def test_the_plan_declares_every_file_it_will_read(workspace: Path) -> None:
    """目标集合必须在裁决之前封闭 (TargetResolution.FORGE_EXPANDED).

    read_paths 少一条, 安全侧就为一个它没见过的路径放行了.
    """
    tool = CodeDefinitionsTool(ResourceGovernor(), NullArtifactStore())
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-plan",
            tool_name="code_definitions",
            arguments={"symbol": "UsageMeter"},
            tool_call_id="call-plan",
        ),
        _context(workspace),
    )

    assert isinstance(plan, ToolPlan)
    read = set(plan.effects.read_paths)
    assert any(path.endswith("metering.py") for path in read)
    assert any(path.endswith("DataSourceConfig.java") for path in read)
    # 认不出语言的文件不进目标集合: 我们压根不打算解析它.
    assert not any(path.endswith("blob.dat") for path in read)
