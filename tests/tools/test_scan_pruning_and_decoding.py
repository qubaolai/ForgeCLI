"""遍历时剪枝, .gitignore 与编码探测.

这三件事的共同点是**失败时没有任何一层会说话**: 额度被生成物吃光, 或者一个 GBK 文件被
解成乱码, 结果都是一句"这是确定的空结果"原样回给模型. 所以它们各要一条用例钉住.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.builtin.search_text import SearchTextTool
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.plan import ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import (
    OsFileSystemView,
    decode_text,
    looks_binary,
)
from support.fakes import PROFILE, NullArtifactStore


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("TARGET_TOKEN = 1\n", encoding="utf-8")
    generated = tmp_path / "out"
    generated.mkdir()
    (generated / "bundle.js").write_text("TARGET_TOKEN\n", encoding="utf-8")
    vendored = tmp_path / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text("TARGET_TOKEN\n", encoding="utf-8")
    return tmp_path


def _context(workspace: Path) -> ExecutionContext:
    return ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _search(workspace: Path, **arguments: object) -> str:
    tool = SearchTextTool(ResourceGovernor(), NullArtifactStore())
    context = _context(workspace)
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-s",
            tool_name="search_text",
            arguments=arguments,
            tool_call_id="call-s",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    result = tool.perform(plan, context)
    return "\n".join(part.text for part in result.content_parts if part.text)


def test_the_builtin_list_still_prunes_without_a_gitignore(workspace: Path) -> None:
    output = _search(workspace, query="TARGET_TOKEN")

    assert "app.py" in output
    assert "node_modules" not in output


def test_a_gitignore_entry_is_honoured(workspace: Path) -> None:
    """`out/` 不在内置名单里. 不读 .gitignore 的话它会被扫进来 —— 而在一个真实项目里
    这种目录的文件数常常比源码多一个数量级, 额度被它吃光之后, "这个词不在代码里"这个
    结论建立在没扫到源码上.
    """
    (workspace / ".gitignore").write_text("out/\n", encoding="utf-8")

    output = _search(workspace, query="TARGET_TOKEN")

    assert "app.py" in output
    assert "bundle.js" not in output


def test_include_ignored_turns_every_rule_off(workspace: Path) -> None:
    """模型显式说"我就是要看生成目录"时, 任何剪枝都是在替它做决定."""
    (workspace / ".gitignore").write_text("out/\n", encoding="utf-8")

    output = _search(workspace, query="TARGET_TOKEN", include_ignored=True)

    assert "bundle.js" in output
    assert "node_modules" in output


def test_the_ignore_decision_comes_from_git_not_a_hardcoded_list(
    workspace: Path,
) -> None:
    """忽略判断由 git 自己给出, 不是我们列的名单.

    在一个 git 仓库里, `.gitignore` 说什么就是什么 —— 包括这个项目自己的生成目录,
    以及嵌套在子目录里的 .gitignore. 写死名单永远认不出后两者.
    """
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    # 一个内置名单绝不会包含的名字, 只有这个项目自己知道它是生成物.
    (workspace / "generated_by_our_codegen").mkdir()
    (workspace / "generated_by_our_codegen" / "x.py").write_text(
        "TARGET_TOKEN\n", encoding="utf-8"
    )
    (workspace / ".gitignore").write_text(
        "generated_by_our_codegen/\n", encoding="utf-8"
    )

    output = _search(workspace, query="TARGET_TOKEN")

    assert "app.py" in output
    assert "generated_by_our_codegen" not in output


def test_a_nested_gitignore_is_honoured(workspace: Path) -> None:
    """git 在每一层目录都认 .gitignore. 原先只读工作区根那一份, 嵌套的静默漏掉."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    nested = workspace / "sub"
    nested.mkdir()
    (nested / "keep.py").write_text("TARGET_TOKEN\n", encoding="utf-8")
    (nested / "skipme.py").write_text("TARGET_TOKEN\n", encoding="utf-8")
    (nested / ".gitignore").write_text("skipme.py\n", encoding="utf-8")

    output = _search(workspace, query="TARGET_TOKEN")

    assert "keep.py" in output
    assert "skipme.py" not in output


def test_pruning_happens_during_the_walk_not_after(tmp_path: Path) -> None:
    """额度数的必须是**幸存的**候选, 不是走过的目录项.

    反过来做的话 `.venv` 与 `node_modules` 会先把 max_results 吃光, 于是一次覆盖整个
    仓库的展开报"结果不完整", 而漏掉的恰好是源码. 这条用例把顺序钉死: 噪音目录里放
    50 个文件, 源码只有 3 个, 额度给 5 —— 事后过滤的实现会一个源码文件都拿不到.
    """
    noise = tmp_path / "node_modules"
    noise.mkdir()
    for index in range(50):
        (noise / f"n{index}.js").write_text("x", encoding="utf-8")
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x", encoding="utf-8")

    found = OsFileSystemView().expand_glob(
        "**/*", root=str(tmp_path), max_results=5, skip_ignored=True
    )

    assert sorted(Path(item).name for item in found) == ["a.py", "b.py", "c.py"]


def test_a_gbk_source_file_is_searchable(tmp_path: Path) -> None:
    """无条件 utf-8/replace 会把它解成乱码, 搜"当前数据源"命中 0 处 —— 然后如实告诉
    模型"这是确定的空结果". 国内 Java 仓库里 GBK 源文件并不罕见.
    """
    body = (
        "package com.example.config;\n"
        "// 当前数据源配置类, 负责在运行时切换主库与从库.\n"
        "// 通过线程上下文保存当前正在使用的数据源标识, 默认走主库.\n"
        "public class DataSourceConfig {\n"
        '    private static final String DEFAULT = "master";  // 默认数据源\n'
        "}\n"
    ) * 4
    (tmp_path / "DataSourceConfig.java").write_bytes(body.encode("gbk"))

    output = _search(tmp_path, query="当前数据源")

    assert "当前数据源" in output
    assert "DataSourceConfig.java" in output


def test_utf8_wins_over_detection(tmp_path: Path) -> None:
    """顺序不能反: charset-normalizer 是统计判断, 短文件上会猜错 (实测 30 字节的 GBK
    片段被判成 cp949). 能严格解成 utf-8 就是确定答案, 不该让它上场.
    """
    assert decode_text("短".encode()) == "短"


def test_a_binary_file_is_skipped_and_counted(tmp_path: Path) -> None:
    """二进制解码出来的替换字符照样会"命中", 于是命中额度被喂给噪音."""
    (tmp_path / "keep.py").write_text("NEEDLE\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00NEEDLE\x00" * 20)

    output = _search(tmp_path, query="NEEDLE")

    assert "keep.py" in output
    assert "blob.bin" not in output
    assert "跳过了 1 个二进制文件" in output


def test_looks_binary_uses_the_nul_byte_rule() -> None:
    assert looks_binary(b"abc\x00def")
    assert not looks_binary("纯中文源码".encode())


def test_no_parameter_name_means_two_things_across_the_retrieval_tools() -> None:
    """三个检索工具之间不能有同名不同义的参数.

    `fs_find.pattern` 与 `search_text.pattern` 曾经是同一个名字的两个意思: 那边是"我要
    找的东西", 这边是"限定在哪些文件里找". 日志里模型因此发出过
    `fs_find(path="frontend", pattern="当前数据源")` —— 想在内容里搜一段文字, 却填进了
    文件名 glob, 然后拿到一句"这是确定的空结果", 于是停止检索改去猜文件全文读.

    ADR-0029 把这类失效叫作"拿 A 工具的 schema 去填 B 工具的参数". 这条用例钉住它不会
    再长回来: 同名参数必须同义, 语义不同就必须换名字.
    """
    from forgecli.application.tools.builtin.code_definitions import (
        _SPEC as definitions_spec,
    )
    from forgecli.application.tools.builtin.fs_find import _SPEC as find_spec
    from forgecli.application.tools.builtin.fs_read import _SPEC as read_spec
    from forgecli.application.tools.builtin.search_text import _SPEC as search_spec

    # 同名参数在两个工具里必须是同一件事. path 是唯一合法的共享名 —— 三处都是"在哪找".
    shared_by_design = {"path", "include_ignored"}
    seen: dict[str, str] = {}
    for spec in (find_spec, search_spec, definitions_spec, read_spec):
        for name in spec.input_schema["properties"]:
            if name in shared_by_design:
                continue
            assert name not in seen, (
                f"{name!r} 同时是 {seen[name]} 和 {spec.name} 的参数; "
                "同名必须同义, 否则模型会拿一个工具的心智去填另一个的 schema"
            )
            seen[name] = spec.name


def test_a_non_glob_name_glob_points_at_the_content_search_tools(
    workspace: Path,
) -> None:
    """把一段内容填进 name_glob 时, 空结果要指回正确的工具.

    改名让这件事更难发生, 但填错仍然可能. 原来的收尾是"目录存在且可读, 这是确定的空
    结果" —— 那句话把一个错误结论替模型钉死了.
    """
    from forgecli.application.tools.builtin.fs_find import FindTool

    tool = FindTool(ResourceGovernor(), NullArtifactStore())
    context = _context(workspace)
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-f",
            tool_name="fs_find",
            arguments={"name_glob": "当前数据源"},
            tool_call_id="call-f",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    output = "\n".join(
        part.text for part in tool.perform(plan, context).content_parts if part.text
    )

    assert "确定的空结果" not in output
    assert "search_text" in output
    assert "code_definitions" in output


def test_a_real_glob_still_gets_the_confident_empty_result(workspace: Path) -> None:
    """写对了 glob 却没命中, 那就是确定的空结果 —— 这时候不该再劝它换工具."""
    from forgecli.application.tools.builtin.fs_find import FindTool

    tool = FindTool(ResourceGovernor(), NullArtifactStore())
    context = _context(workspace)
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-g",
            tool_name="fs_find",
            arguments={"name_glob": "**/*.rs"},
            tool_call_id="call-g",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    output = "\n".join(
        part.text for part in tool.perform(plan, context).content_parts if part.text
    )

    assert "确定的空结果" in output
