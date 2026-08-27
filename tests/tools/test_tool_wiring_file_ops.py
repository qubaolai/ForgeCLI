from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from forgecli.application.tools.builtin.fs_apply_patch import ApplyPatchTool
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResultStatus
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from forgecli.interfaces.runtime.tool_wiring import (
    _create_file,
    _delete_file,
    _make_directory,
    _move_file,
    _replace_file,
)
from support.fakes import PROFILE


def test_create_never_overwrites_a_competing_target(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _create_file(target, "replace")

    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_replace_preserves_executable_mode(tmp_path: Path) -> None:
    target = tmp_path / "script.sh"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o755)

    _replace_file(target, "new")

    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_move_never_overwrites_a_competing_target(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("source", encoding="utf-8")
    target.write_text("target", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _move_file(str(source), str(target))

    assert source.read_text(encoding="utf-8") == "source"
    assert target.read_text(encoding="utf-8") == "target"


def test_make_directory_tolerates_an_existing_directory(tmp_path: Path) -> None:
    target = tmp_path / "backend"
    target.mkdir()

    _make_directory(str(target))

    assert target.is_dir()


def test_envelope_with_a_shared_new_ancestor_applies_every_section(
    tmp_path: Path,
) -> None:
    """同一个信封里两段共用一个新建祖先目录, 两段都要落盘.

    每一段的父目录清单都是 prepare 阶段按"这一段执行前"的磁盘状态算的, 于是第二段的
    清单里仍然写着第一段刚建过的 backend/. 这里接的是真实的文件操作函数而不是测试替身
    —— 这个缺陷正是从替身与接线的差异里漏过去的: 替身一直是 exist_ok=True.
    """
    tool = ApplyPatchTool(
        _create_file, _replace_file, _delete_file, _move_file, _make_directory
    )
    context = ExecutionContext(
        cwd=str(tmp_path),
        workspace_roots=(str(tmp_path),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    patch = (
        "*** NEW backend/pom.xml\n<project/>\n\n"
        "*** NEW backend/src/Main.java\nclass Main {}"
    )
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="fs_apply_patch",
            arguments={"patch": patch},
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan)

    result = tool.perform(plan, context)

    assert result.status is ToolResultStatus.OK
    assert (tmp_path / "backend/pom.xml").read_text(encoding="utf-8") == "<project/>\n"
    assert (tmp_path / "backend/src/Main.java").read_text(
        encoding="utf-8"
    ) == "class Main {}\n"
