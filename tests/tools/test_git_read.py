"""git_read 的工具层约定 (ADR-0040 决策 4.4).

换掉的实现靠一张 CLI 参数白名单保证只读: 挡 `--ext-diff` / `--textconv` (跑外部程序),
挡 `--output=` (写文件), 挡 `-c core.pager=` (指定任意命令), 挡 `remote show` (联网 +
调凭证帮助器). 原来的用例逐条钉着那些参数.

现在没有参数可挡了 —— 查询走进程内的库调用, 模型能给出的只有 schema 里列着的字段.
所以这些用例钉的是**替代那张白名单的性质**: 不起子进程, 不接受表外的查询, 也不接受
schema 之外的任何字段.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from forgecli.application.tools.builtin.git_read import GitReadTool
from forgecli.application.tools.git_queries import (
    GitBlameHunk,
    GitBranch,
    GitCommit,
    GitDiffStats,
    GitPatch,
    GitQueries,
    GitQueryError,
    GitRemote,
    GitStash,
    GitStatusEntry,
    GitUnsupported,
)
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResultStatus
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE, NullArtifactStore

EMPTY_PATCH = GitPatch(text="", stats=GitDiffStats(0, 0, 0))


class _StubQueries(GitQueries):
    """按用例给定的返回值应答; 也记录被调用时收到的参数."""

    def __init__(self, **answers: object) -> None:
        self.answers = answers
        self.seen: dict[str, object] = {}

    def _answer(self, name: str, default: object) -> object:
        value = self.answers.get(name, default)
        if isinstance(value, Exception):
            raise value
        return value

    def status(self, root: str) -> tuple[GitStatusEntry, ...]:
        return self._answer("status", ())  # type: ignore[return-value]

    def diff(
        self,
        root: str,
        *,
        staged: bool = False,
        paths: Sequence[str] = (),
        context_lines: int = 3,
    ) -> GitPatch:
        self.seen = {
            "staged": staged,
            "paths": tuple(paths),
            "context_lines": context_lines,
        }
        return self._answer("diff", EMPTY_PATCH)  # type: ignore[return-value]

    def log(
        self,
        root: str,
        *,
        revision: str | None = None,
        max_count: int = 20,
        paths: Sequence[str] = (),
    ) -> tuple[GitCommit, ...]:
        self.seen = {
            "revision": revision,
            "max_count": max_count,
            "paths": tuple(paths),
        }
        return self._answer("log", ())  # type: ignore[return-value]

    def show(self, root: str, revision: str) -> tuple[GitCommit, GitPatch]:
        self.seen = {"revision": revision}
        return self._answer(  # type: ignore[return-value]
            "show",
            (GitCommit("abc1234", "a", "2026-01-01T00:00:00+00:00", "s"), EMPTY_PATCH),
        )

    def blame(
        self,
        root: str,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> tuple[GitBlameHunk, ...]:
        self.seen = {"path": path, "start_line": start_line, "end_line": end_line}
        return self._answer("blame", ())  # type: ignore[return-value]

    def branches(
        self, root: str, *, include_remote: bool = False
    ) -> tuple[GitBranch, ...]:
        self.seen = {"include_remote": include_remote}
        return self._answer("branches", ())  # type: ignore[return-value]

    def remotes(self, root: str) -> tuple[GitRemote, ...]:
        return self._answer("remotes", ())  # type: ignore[return-value]

    def stashes(self, root: str) -> tuple[GitStash, ...]:
        return self._answer("stashes", ())  # type: ignore[return-value]


def _context(tmp_path: Path) -> ExecutionContext:
    root = tmp_path / "workspace"
    root.mkdir()
    return ExecutionContext(
        cwd=str(root),
        workspace_roots=(str(root),),
        environment={"HOME": str(tmp_path / "home")},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _run(
    queries: GitQueries, context: ExecutionContext, **arguments: object
) -> tuple[ToolPlan | PreparationError, object]:
    tool = GitReadTool(queries, ResourceGovernor(), NullArtifactStore())
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-git",
            tool_name="git_read",
            arguments=arguments,
            tool_call_id="call-git",
        ),
        context,
    )
    if isinstance(plan, PreparationError):
        return (plan, None)
    return (plan, tool.perform(plan, context))


def _text(result: object) -> str:
    return "".join(part.text for part in result.content_parts)  # type: ignore[attr-defined]


# ---- 替代白名单的那些性质 ----


def test_the_tool_no_longer_declares_spawn_process(tmp_path: Path) -> None:
    """这条是整次改动的实质: 读 git 不再需要"我会起子进程"这个能力上界."""
    tool = GitReadTool(_StubQueries(), ResourceGovernor(), NullArtifactStore())
    assert Capability.SPAWN_PROCESS not in tool.spec.declared_capabilities
    assert tool.spec.declared_capabilities == frozenset({Capability.WORKSPACE_READ})


def test_the_plan_declares_no_child_process(tmp_path: Path) -> None:
    plan, _ = _run(_StubQueries(), _context(tmp_path), query="status")
    assert isinstance(plan, ToolPlan)
    assert plan.effects.child_process is False


def test_an_unknown_query_is_refused(tmp_path: Path) -> None:
    plan, _ = _run(_StubQueries(), _context(tmp_path), query="push")
    assert isinstance(plan, PreparationError)


def test_raw_cli_options_have_nowhere_to_go(tmp_path: Path) -> None:
    """schema 是 additionalProperties: false, 所以塞不进 "args" 这种自由字段.

    这条替代的是原来那一整张选项白名单: 挡住 `--ext-diff` 的办法不再是列举它, 而是
    让"传一个选项"这件事在协议上就不存在.
    """
    plan, _ = _run(
        _StubQueries(),
        _context(tmp_path),
        query="diff",
        args=["--ext-diff", "--output=/tmp/x"],
    )
    assert isinstance(plan, PreparationError)


def test_blame_without_a_path_is_refused(tmp_path: Path) -> None:
    plan, _ = _run(_StubQueries(), _context(tmp_path), query="blame")
    assert isinstance(plan, PreparationError)


# ---- 参数确实传到了查询层 ----


def test_diff_arguments_reach_the_query(tmp_path: Path) -> None:
    queries = _StubQueries()
    _run(
        queries,
        _context(tmp_path),
        query="diff",
        staged=True,
        paths=["src", "docs"],
        context_lines=0,
    )
    assert queries.seen == {
        "staged": True,
        "paths": ("src", "docs"),
        "context_lines": 0,
    }


def test_log_arguments_reach_the_query(tmp_path: Path) -> None:
    queries = _StubQueries()
    _run(queries, _context(tmp_path), query="log", revision="main", max_count=5)
    assert queries.seen == {"revision": "main", "max_count": 5, "paths": ()}


def test_log_defaults_are_bounded(tmp_path: Path) -> None:
    """不给 max_count 时也不能变成"走完整段历史"."""
    queries = _StubQueries()
    _run(queries, _context(tmp_path), query="log")
    assert queries.seen["max_count"] == 20
    assert queries.seen["revision"] is None


def test_show_defaults_to_head(tmp_path: Path) -> None:
    queries = _StubQueries()
    _run(queries, _context(tmp_path), query="show")
    assert queries.seen == {"revision": "HEAD"}


def test_blame_line_range_reaches_the_query(tmp_path: Path) -> None:
    queries = _StubQueries()
    _run(
        queries,
        _context(tmp_path),
        query="blame",
        path="src/a.py",
        start_line=10,
        end_line=20,
    )
    assert queries.seen == {"path": "src/a.py", "start_line": 10, "end_line": 20}


# ---- 空结果要说话 ----


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("status", "工作区干净"),
        ("diff", "没有改动"),
        ("log", "没有匹配的提交"),
        ("branches", "没有分支"),
        ("remotes", "没有配置远端"),
        ("stashes", "没有 stash"),
    ],
)
def test_empty_results_say_so(tmp_path: Path, query: str, expected: str) -> None:
    """空串会让模型分不清"没有内容"和"这次调用失败了" —— 两者该走不同的下一步."""
    _, result = _run(_StubQueries(), _context(tmp_path), query=query)
    assert result.status is ToolResultStatus.OK  # type: ignore[attr-defined]
    assert expected in _text(result)


# ---- 渲染 ----


def test_status_renders_two_status_columns(tmp_path: Path) -> None:
    entries = (
        GitStatusEntry(path="a.py", index="M", worktree=" "),
        GitStatusEntry(path="b.py", index=" ", worktree="?"),
    )
    _, result = _run(_StubQueries(status=entries), _context(tmp_path), query="status")
    body = _text(result)
    assert "M  a.py" in body
    assert " ? b.py" in body


def test_diff_leads_with_the_stat_line(tmp_path: Path) -> None:
    patch = GitPatch(text="@@ -1 +1 @@\n-a\n+b\n", stats=GitDiffStats(1, 1, 1))
    _, result = _run(_StubQueries(diff=patch), _context(tmp_path), query="diff")
    body = _text(result)
    assert "1 个文件, +1 -1" in body
    assert "@@ -1 +1 @@" in body


def test_log_renders_one_commit_per_line(tmp_path: Path) -> None:
    commits = (
        GitCommit("abc1234", "almond", "2026-08-27T13:33:13+08:00", "第一条"),
        GitCommit("def5678", "almond", "2026-08-26T10:00:00+08:00", "第二条"),
    )
    _, result = _run(_StubQueries(log=commits), _context(tmp_path), query="log")
    lines = _text(result).strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("abc1234  2026-08-27T13:33:13+08:00  almond  第一条")


def test_blame_renders_a_line_range(tmp_path: Path) -> None:
    hunks = (GitBlameHunk(start_line=4, line_count=3, short_id="abc1234", author="a"),)
    _, result = _run(
        _StubQueries(blame=hunks), _context(tmp_path), query="blame", path="x.py"
    )
    assert "abc1234  a  L4-6" in _text(result)


def test_branches_mark_the_current_one(tmp_path: Path) -> None:
    branches = (
        GitBranch(name="main", is_head=True, is_remote=False, short_id="abc1234"),
        GitBranch(name="side", is_head=False, is_remote=False, short_id="def5678"),
    )
    _, result = _run(
        _StubQueries(branches=branches), _context(tmp_path), query="branches"
    )
    body = _text(result)
    assert "* main  abc1234" in body
    assert "  side  def5678" in body


# ---- 失败 ----


def test_a_failed_query_becomes_a_tool_error(tmp_path: Path) -> None:
    queries = _StubQueries(status=GitQueryError("不在一个 git 仓库里"))
    _, result = _run(queries, _context(tmp_path), query="status")
    assert result.status is ToolResultStatus.TOOL_ERROR  # type: ignore[attr-defined]
    assert result.error.code == "git_failed"  # type: ignore[attr-defined]
    assert "不在一个 git 仓库里" in result.error.message  # type: ignore[attr-defined]


def test_unsupported_gets_its_own_code(tmp_path: Path) -> None:
    """ "我们做不到"和"这个仓库有问题"要分得开: 前者的出路是 shell_run, 后者是修仓库."""
    queries = _StubQueries(show=GitUnsupported("不是一个提交"))
    _, result = _run(queries, _context(tmp_path), query="show", revision="HEAD^{tree}")
    assert result.error.code == "git_unsupported"  # type: ignore[attr-defined]
