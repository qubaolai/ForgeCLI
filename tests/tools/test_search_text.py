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


# ---- 生成目录的过滤 ----
#
# 与 fs.list_files 共用 base.filter_globbed. 两份实现一定会走偏, 而走偏的后果是同一个
# 仓库在两个工具眼里有不同的形状.


@pytest.fixture
def workspace_with_build_output(workspace: Path) -> Path:
    """一个 Maven 项目的形状: 源码在 src, 编译产物在 target."""
    generated = workspace / "target" / "classes"
    generated.mkdir(parents=True)
    (generated / "Auth.java").write_text("def login(user):", encoding="utf-8")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "x.js").write_text(
        "def login(user):", encoding="utf-8"
    )
    return workspace


def test_generated_directories_are_skipped(workspace_with_build_output: Path) -> None:
    """target/ 与 node_modules/ 里的同名命中不该出现.

    这不只是噪音问题: 扫描有 2000 个文件的上限, 一个 Maven 项目的 target/ 就能把额度
    吃光, 于是"这个词不在代码里"这个结论建立在压根没扫到源码上 —— 一个假的空结果比
    没有结果危险得多.
    """
    output = _run(workspace_with_build_output, query="def login")
    assert "auth.py" in output
    assert "target" not in output
    assert "node_modules" not in output


def test_include_ignored_brings_them_back(workspace_with_build_output: Path) -> None:
    output = _run(workspace_with_build_output, query="def login", include_ignored=True)
    assert "target" in output
    assert "node_modules" in output


def test_pointing_path_at_a_generated_directory_still_works(
    workspace_with_build_output: Path,
) -> None:
    """段的起点是 path 本身, 所以显式指到 target/ 里仍然搜得到.

    这是 include_ignored 之外的另一条逃生通道: 想搜编译产物的人往往知道自己在搜哪儿.
    """
    output = _run(
        workspace_with_build_output,
        query="def login",
        path=str(workspace_with_build_output / "target"),
    )
    assert "Auth.java" in output


def test_the_empty_message_mentions_the_filter(workspace: Path) -> None:
    """空结果要说清"可能是被过滤掉了", 否则模型只会换个 pattern 再搜一次."""
    output = _run(workspace, query="def login", pattern="**/*.kt")
    assert "include_ignored" in output


# ---- 进度行的字节数 ----


def _run_result(workspace: Path, **arguments: object) -> object:
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
    return tool.perform(plan, context)


def test_the_result_reports_how_many_bytes_it_produced(workspace: Path) -> None:
    """终端进度行读 metrics.bytes_out; 不填就是每个工具都显示 `0 字节`.

    那条线是用户判断"工具到底有没有拿回内容"的唯一依据 —— 见过 fs.read_file 读完一个
    Java 文件显示 0 字节, 而模型在回答里引用了里面的代码.
    """
    result = _run_result(workspace, query="def login")

    assert result.metrics.bytes_out > 0  # type: ignore[attr-defined]
    assert result.metrics.bytes_out == len(result.text.encode("utf-8"))  # type: ignore[attr-defined]
