"""补丁 UPDATE 段的容差边界: 容忍不携带信息的差异, 不容忍携带信息的差异.

工具从 `fs.edit_file` 换成了 `fs.apply_patch` (ADR-0029 C 类), 但**容差判据一条不改** ——
下面每条用例的输入输出都与合并前一致, 只是 old_string/new_string 改成信封里的
FIND/REPLACE 段.

`old_string` 来自模型上一次读到的内容, 而中间隔着一层它看不见的东西 —— 文件是 CRLF 还是
LF, 行尾有没有多余空格, 它复述这段代码时把整块的基准缩进带上了没有. 这几样都不改变代码
的含义, 却足以让逐字符比对失败. 而失败之后模型看不出差在哪, 只能换个写法再试, 或者绕去
shell 改文件.

所以这里两个方向都要钉住: 该过的必须过 (否则模型回到 shell), 该拦的必须拦 (按偏移硬贴
会写出缩进错乱的代码, 在 Python 与 YAML 里那就是改坏了).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.builtin.fs_apply_patch import ApplyPatchTool
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _envelope(path: object, old: object, new: object, every: object = False) -> str:
    """把一次替换写成信封. 保留旧用例的调用形状, 只换入参编码方式."""
    marker = "*** FIND ALL" if every else "*** FIND"
    return f"*** UPDATE {path}\n{marker}\n{old}\n*** REPLACE\n{new}"


def _edit(workspace: Path, **arguments: object) -> ToolPlan | PreparationError:
    tool = ApplyPatchTool(
        lambda path, content: Path(path).write_text(content, "utf-8"),
        lambda path, content: Path(path).write_text(content, "utf-8"),
        lambda path: Path(path).unlink(),
        lambda source, target: Path(source).rename(target),
        lambda path: Path(path).mkdir(parents=True, exist_ok=True),
    )
    patch = _envelope(
        arguments["path"],
        arguments.get("old_string", ""),
        arguments.get("new_string", ""),
        arguments.get("replace_all", False),
    )
    return tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="fs.apply_patch",
            arguments={"patch": patch},
            tool_call_id="c1",
        ),
        ExecutionContext(
            cwd=str(workspace),
            workspace_roots=(str(workspace),),
            environment={"PATH": "/usr/bin:/bin"},
            filesystem=OsFileSystemView(),
            profile=PROFILE,
        ),
    )


def _first(plan: ToolPlan) -> dict[str, object]:
    """信封里的第一个操作. 旧用例只改一个文件, 所以永远看第一个就够."""
    operations = plan.normalized_input["operations"]
    assert isinstance(operations, tuple)
    return dict(operations[0])


def _write(workspace: Path, name: str, text: str) -> Path:
    target = workspace / name
    target.write_bytes(text.encode("utf-8"))
    return target


# ---- 该过的 ----


def test_a_crlf_file_edits_with_lf_input(workspace: Path) -> None:
    """模型永远写 \\n; 文件是 CRLF 的话逐字符比对必然失败, 而这个差异没有任何含义."""
    _write(workspace, "A.java", "class A {\r\n    int x = 1;\r\n}\r\n")

    plan = _edit(
        workspace,
        path="A.java",
        old_string="    int x = 1;\n",
        new_string="    int x = 1;\n    int y = 2;\n",
    )

    assert isinstance(plan, ToolPlan)
    content = str(_first(plan)["content"])
    assert "int y = 2;" in content
    # 没碰到的那些行必须逐字节不变 —— 顺手把整个文件换行统一了, 等于改了没打算改的地方.
    assert content.startswith("class A {\r\n")
    assert content.endswith("}\r\n")
    # 新写进去的行也要用文件在用的那种换行, 否则文件从此混着两种,
    # git diff 里整块都算改过.
    assert "\n" not in content.replace("\r\n", "")


def test_trailing_whitespace_in_the_file_does_not_block_the_edit(
    workspace: Path,
) -> None:
    _write(workspace, "a.py", "def f():   \n    return 1\n")

    plan = _edit(
        workspace, path="a.py", old_string="def f():\n", new_string="def g():\n"
    )

    assert isinstance(plan, ToolPlan)
    assert str(_first(plan)["content"]).startswith("def g():")


def test_a_block_written_without_its_leading_indent_is_aligned(
    workspace: Path,
) -> None:
    """模型常把一段代码"平着"复述出来, 丢掉整块的公共缩进."""
    _write(
        workspace,
        "A.java",
        "class A {\n    void f() {\n        int x = 1;\n    }\n}\n",
    )

    plan = _edit(
        workspace,
        path="A.java",
        old_string="void f() {\n    int x = 1;\n}\n",
        new_string="void f() {\n    int x = 1;\n    int y = 2;\n}\n",
    )

    assert isinstance(plan, ToolPlan)
    assert str(_first(plan)["content"]) == (
        "class A {\n    void f() {\n        int x = 1;\n        int y = 2;\n    }\n}\n"
    )


def test_the_same_shift_is_applied_to_the_replacement(workspace: Path) -> None:
    """只对齐匹配位置而把 new_string 原样贴进去, 产出的是缩进错乱的代码."""
    _write(workspace, "a.py", "class C:\n    def f(self):\n        return 1\n")

    plan = _edit(
        workspace,
        path="a.py",
        old_string="def f(self):\n    return 1\n",
        new_string="def f(self):\n    value = 1\n    return value\n",
    )

    assert isinstance(plan, ToolPlan)
    assert str(_first(plan)["content"]) == (
        "class C:\n    def f(self):\n        value = 1\n        return value\n"
    )


def test_an_over_indented_fragment_is_aligned_too(workspace: Path) -> None:
    _write(workspace, "a.py", "def f():\n    return 1\n")

    plan = _edit(
        workspace,
        path="a.py",
        old_string="    def f():\n        return 1\n",
        new_string="    def f():\n        return 2\n",
    )

    assert isinstance(plan, ToolPlan)
    assert str(_first(plan)["content"]) == "def f():\n    return 2\n"


def test_the_model_is_told_that_its_copy_was_out_of_date(workspace: Path) -> None:
    """不说, 模型下一次还会照着自己那份去拼, 而下一次未必还落在容差范围内."""
    _write(workspace, "a.py", "class C:\n    def f(self):\n        return 1\n")

    plan = _edit(
        workspace,
        path="a.py",
        old_string="def f(self):\n    return 1\n",
        new_string="def f(self):\n    return 2\n",
    )

    assert isinstance(plan, ToolPlan)
    note = str(_first(plan)["note"])
    assert "缩进" in note
    assert "重新读一遍" in note


def test_a_single_unindented_line_still_matches_as_a_substring(
    workspace: Path,
) -> None:
    """不带缩进的单行片段本来就是文件的子串, 走的仍是逐字符那条路, 不需要容差."""
    _write(workspace, "a.py", "class C:\n    x = 1\n")

    plan = _edit(workspace, path="a.py", old_string="x = 1\n", new_string="x = 2\n")

    assert isinstance(plan, ToolPlan)
    assert str(_first(plan)["content"]) == "class C:\n    x = 2\n"
    assert _first(plan)["note"] == ""


def test_an_exact_match_carries_no_note(workspace: Path) -> None:
    _write(workspace, "a.py", "x = 1\n")

    plan = _edit(workspace, path="a.py", old_string="x = 1", new_string="x = 2")

    assert isinstance(plan, ToolPlan)
    assert _first(plan)["note"] == ""


# ---- 该拦的 ----


def test_tabs_against_spaces_quotes_the_file_verbatim(workspace: Path) -> None:
    """缩进方式不同不能按偏移硬贴; 引出原文比描述差异有用 —— 后者还是要模型自己猜."""
    _write(workspace, "A.java", "class A {\n\t\tint x = 1;\n}\n")

    error = _edit(
        workspace,
        path="A.java",
        old_string="        int x = 1;\n",
        new_string="        int x = 2;\n",
    )

    assert isinstance(error, PreparationError)
    assert "\t\tint x = 1;" in error.message
    assert "照抄" in error.message


def test_a_fragment_whose_inner_indent_differs_is_refused(workspace: Path) -> None:
    """块内相对缩进对不上就不是同一段代码, 硬贴会把结构改坏."""
    _write(workspace, "a.py", "def f():\n    if x:\n        return 1\n")

    error = _edit(
        workspace,
        path="a.py",
        old_string="def f():\n  if x:\n      return 1\n",
        new_string="def f():\n  if x:\n      return 2\n",
    )

    assert isinstance(error, PreparationError)


def test_two_aligned_matches_still_need_find_all(workspace: Path) -> None:
    """容差不放宽唯一性: 命中多处时模型想改的几乎总是其中一处.

    表达方式从 `replace_all=true` 换成 `*** FIND ALL` (ADR-0029: 变化在信封语法里
    表达, 不加参数), 判据不变 —— 命中多处又没明说要全改, 就是报错.
    """
    _write(workspace, "a.py", "class A:\n    x = 1\nclass B:\n    x = 1\n")

    error = _edit(workspace, path="a.py", old_string="x = 1\n", new_string="x = 2\n")

    assert isinstance(error, PreparationError)
    assert "2 次" in error.message
    assert "FIND ALL" in error.message


def test_find_all_applies_to_every_aligned_match(workspace: Path) -> None:
    _write(workspace, "a.py", "class A:\n    x = 1\nclass B:\n    x = 1\n")

    plan = _edit(
        workspace,
        path="a.py",
        old_string="x = 1\n",
        new_string="x = 2\n",
        replace_all=True,
    )

    assert isinstance(plan, ToolPlan)
    assert str(_first(plan)["content"]) == (
        "class A:\n    x = 2\nclass B:\n    x = 2\n"
    )


def test_a_fragment_that_is_simply_absent_still_fails(workspace: Path) -> None:
    _write(workspace, "a.py", "x = 1\n")

    error = _edit(workspace, path="a.py", old_string="缺失的一行", new_string="y")

    assert isinstance(error, PreparationError)
    assert "逐字符一致" in error.message


def test_matching_never_swallows_the_following_line(workspace: Path) -> None:
    """old_string 末尾没有换行时, 文件的换行不该被吞掉 —— 否则两行会粘在一起."""
    _write(workspace, "a.py", "class C:\n    x = 1\n    y = 2\n")

    plan = _edit(workspace, path="a.py", old_string="x = 1", new_string="x = 9")

    assert isinstance(plan, ToolPlan)
    assert str(_first(plan)["content"]) == "class C:\n    x = 9\n    y = 2\n"


def test_a_lone_lf_file_is_not_given_crlf(workspace: Path) -> None:
    """跟随文件, 不是一律转成某一种."""
    _write(workspace, "a.py", "class C:\n    def f(self):\n        return 1\n")

    plan = _edit(
        workspace,
        path="a.py",
        old_string="def f(self):\n    return 1\n",
        new_string="def f(self):\n    return 2\n",
    )

    assert isinstance(plan, ToolPlan)
    assert "\r" not in str(_first(plan)["content"])
