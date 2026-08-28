"""GitQueries 的 libgit2 实现, 对着一个真仓库跑 (ADR-0040 决策 4.4).

不测 libgit2 自己的算法 —— 那是它的事. 测的是 Forge 这一层: 输入怎么映射过去, 输出
怎么归一回来, 以及失败往哪个方向倒.

仓库是用 pygit2 自己搭的, 不起 git 进程: 这些用例要在没装 git 的机器上也能跑, 而
"不需要 git 可执行文件"正是这次改动换来的东西.
"""

from __future__ import annotations

from pathlib import Path

import pygit2
import pytest

from forgecli.application.tools.git_queries import GitQueryError, GitUnsupported
from forgecli.infrastructure.workspace.pygit2_git_queries import Pygit2GitQueries

_WHO = pygit2.Signature("Tester", "tester@example.invalid", 1_700_000_000, 0)


def _commit(repository: pygit2.Repository, message: str) -> str:
    repository.index.add_all()
    repository.index.write()
    tree = repository.index.write_tree()
    parents = [] if repository.head_is_unborn else [repository.head.target]
    oid = repository.create_commit("HEAD", _WHO, _WHO, message, tree, parents)
    return str(oid)


@pytest.fixture
def repository(tmp_path: Path) -> pygit2.Repository:
    repo = pygit2.init_repository(str(tmp_path / "work"), initial_head="main")
    root = Path(repo.workdir)
    (root / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "b.txt").write_text("b\n", encoding="utf-8")
    _commit(repo, "第一条提交")
    return repo


@pytest.fixture
def queries() -> Pygit2GitQueries:
    return Pygit2GitQueries()


def _root(repository: pygit2.Repository) -> str:
    return str(repository.workdir)


# ---- 不是仓库 ----


def test_a_directory_outside_any_repository_fails_clearly(
    queries: Pygit2GitQueries, tmp_path: Path
) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    with pytest.raises(GitQueryError, match="不在一个 git 仓库里"):
        queries.status(str(outside))


# ---- status ----


def test_status_reports_clean_after_a_commit(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    assert queries.status(_root(repository)) == ()


def test_status_separates_index_and_worktree_columns(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    root = Path(repository.workdir)
    (root / "a.txt").write_text("changed\n", encoding="utf-8")
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    repository.index.add("a.txt")
    repository.index.write()

    entries = {entry.path: entry for entry in queries.status(_root(repository))}
    assert entries["a.txt"].index == "M"
    assert entries["a.txt"].worktree == " "
    assert entries["new.txt"].worktree == "?"


def test_status_skips_ignored_files(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    """忽略是用户数据, 不是噪音: 列出来只会把真正的改动淹掉."""
    root = Path(repository.workdir)
    (root / ".gitignore").write_text("junk/\n", encoding="utf-8")
    (root / "junk").mkdir()
    (root / "junk" / "x.o").write_text("x", encoding="utf-8")
    _commit(repository, "加 gitignore")

    assert [entry.path for entry in queries.status(_root(repository))] == []


# ---- diff ----


def test_diff_sees_unstaged_changes(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    (Path(repository.workdir) / "a.txt").write_text(
        "one\nTWO\nthree\n", encoding="utf-8"
    )
    patch = queries.diff(_root(repository))
    assert patch.stats.files_changed == 1
    assert "+TWO" in patch.text


def test_staged_and_unstaged_are_different_questions(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    (Path(repository.workdir) / "a.txt").write_text("staged\n", encoding="utf-8")
    repository.index.add("a.txt")
    repository.index.write()

    assert queries.diff(_root(repository), staged=True).stats.files_changed == 1
    assert queries.diff(_root(repository), staged=False).stats.files_changed == 0


def test_diff_can_be_narrowed_to_paths(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    root = Path(repository.workdir)
    (root / "a.txt").write_text("x\n", encoding="utf-8")
    (root / "src" / "b.txt").write_text("y\n", encoding="utf-8")

    narrowed = queries.diff(_root(repository), paths=["src"])
    assert narrowed.stats.files_changed == 1
    assert "src/b.txt" in narrowed.text
    assert "a.txt\n" not in narrowed.text.replace("src/b.txt", "")


def test_diff_of_a_clean_tree_is_empty_not_an_error(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    patch = queries.diff(_root(repository))
    assert patch.stats.files_changed == 0
    assert patch.text == ""


# ---- log ----


def test_log_is_newest_first_and_bounded(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    root = Path(repository.workdir)
    for index in range(3):
        (root / f"f{index}.txt").write_text(str(index), encoding="utf-8")
        _commit(repository, f"提交 {index}")

    commits = queries.log(_root(repository), max_count=2)
    assert [c.summary for c in commits] == ["提交 2", "提交 1"]


def test_log_carries_author_and_an_iso_timestamp(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    commit = queries.log(_root(repository), max_count=1)[0]
    assert commit.author == "Tester"
    assert commit.committed_at.startswith("2023-")  # 1_700_000_000 落在 2023


def test_log_can_be_narrowed_to_paths(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    root = Path(repository.workdir)
    (root / "src" / "b.txt").write_text("changed\n", encoding="utf-8")
    _commit(repository, "只动 src")
    (root / "a.txt").write_text("changed\n", encoding="utf-8")
    _commit(repository, "只动根")

    summaries = [c.summary for c in queries.log(_root(repository), paths=["src"])]
    assert "只动 src" in summaries
    assert "只动根" not in summaries


def test_an_unresolvable_revision_fails_clearly(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    with pytest.raises(GitQueryError, match="解析不了版本"):
        queries.log(_root(repository), revision="no-such-branch")


# ---- show ----


def test_show_returns_the_commit_and_its_patch(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    (Path(repository.workdir) / "a.txt").write_text("second\n", encoding="utf-8")
    _commit(repository, "第二条提交")

    commit, patch = queries.show(_root(repository), "HEAD")
    assert commit.summary == "第二条提交"
    assert "+second" in patch.text


def test_showing_the_first_commit_treats_everything_as_added(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    """首个提交没有父提交. 拿它和空树比, 而不是报错."""
    commit, patch = queries.show(_root(repository), "HEAD")
    assert commit.summary == "第一条提交"
    assert patch.stats.files_changed == 2
    assert patch.stats.deletions == 0


def test_showing_a_tree_is_unsupported_rather_than_wrong(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    with pytest.raises(GitUnsupported):
        queries.show(_root(repository), "HEAD^{tree}")


# ---- blame ----


def test_blame_attributes_lines_to_a_commit(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    hunks = queries.blame(_root(repository), "a.txt")
    assert hunks
    assert hunks[0].author == "Tester"
    assert hunks[0].start_line == 1


def test_blame_can_be_narrowed_to_a_line_range(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    hunks = queries.blame(_root(repository), "a.txt", start_line=2, end_line=3)
    assert all(hunk.start_line >= 1 for hunk in hunks)
    assert hunks


def test_blaming_a_missing_file_fails_clearly(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    with pytest.raises(GitQueryError, match="blame"):
        queries.blame(_root(repository), "nope.txt")


# ---- 引用 ----


def test_branches_mark_head(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    repository.branches.local.create("side", repository[repository.head.target])
    found = {branch.name: branch for branch in queries.branches(_root(repository))}
    assert found["main"].is_head is True
    assert found["side"].is_head is False
    assert found["side"].is_remote is False


def test_remote_urls_are_read_from_local_config_only(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    """只读配置, 不联网 —— 换掉的实现要专门禁掉 `git remote show` 才能保证这一点."""
    repository.remotes.create("origin", "https://example.invalid/x.git")
    remotes = queries.remotes(_root(repository))
    assert [(remote.name, remote.url) for remote in remotes] == [
        ("origin", "https://example.invalid/x.git")
    ]


def test_no_stashes_is_an_empty_tuple(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    assert queries.stashes(_root(repository)) == ()


def test_a_stash_shows_up_with_its_index(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    (Path(repository.workdir) / "a.txt").write_text("dirty\n", encoding="utf-8")
    repository.stash(_WHO, message="存一下")

    stashes = queries.stashes(_root(repository))
    assert len(stashes) == 1
    assert stashes[0].index == 0
    assert "存一下" in stashes[0].message


# ---- 仓库在上层目录 ----


def test_a_subdirectory_still_finds_the_repository(
    queries: Pygit2GitQueries, repository: pygit2.Repository
) -> None:
    """工作区是仓库子目录时也能用 —— 与换掉的实现 (git 以 cwd 启动) 行为一致."""
    subdirectory = Path(repository.workdir) / "src"
    assert queries.log(str(subdirectory), max_count=1)[0].summary == "第一条提交"
