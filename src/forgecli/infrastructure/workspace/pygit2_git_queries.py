"""GitQueries 的 libgit2 实现 (ADR-0040 决策 4.4).

pygit2 的对象一律不出这个文件: 上面拿到的是 `git_queries` 里那几个纯数据类. 换掉
pygit2 只需要换这一个文件 (ADR-0040 决策 3).

## 与 git CLI 的差异, 以及为什么可以接受

libgit2 不是 git 的另一份实现, 是同一套数据格式的另一个读法. 对象, 索引, 引用和
gitignore 的语义一致; 不一致的地方集中在**外部程序入口**:

- 不做 textconv / ext-diff: 二进制文件的补丁就是"Binary files differ", 不会去跑用户
  配置的转换程序;
- 不分页, 不着色, 不读 `core.pager`;
- 不跑 clean/smudge 过滤器里的外部命令.

这些正是 `git_read` 原先要靠 CLI 参数白名单挡住的东西. 换句话说, 差异的方向是**少了
执行入口**, 而不是少了信息.

## 仓库发现会往上走

`discover_repository` 从 root 往上找 `.git`, 所以工作区是仓库子目录时也能用 —— 这和
原先 `git` 以 cwd 启动的行为一致. 代价也一致: 仓库根可能在工作区之外. 这一条没有随
本次改动变化, 授权侧仍按工具声明的 read_paths 裁决.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone

import pygit2
from pygit2.enums import FileStatus, SortMode

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

__all__ = ["Pygit2GitQueries"]

# libgit2 抛出来的一切. pygit2 在参数不合法时抛 ValueError, 找不到对象时抛 KeyError,
# 底层出错时抛 GitError, 文件读不了时抛 OSError —— 四种都归成"这次查询失败".
_LIBGIT2_ERRORS = (pygit2.GitError, KeyError, ValueError, OSError)

# `git status --short` 第一列 (暂存区) 的字符.
_INDEX_MARKS: tuple[tuple[FileStatus, str], ...] = (
    (FileStatus.INDEX_NEW, "A"),
    (FileStatus.INDEX_MODIFIED, "M"),
    (FileStatus.INDEX_DELETED, "D"),
    (FileStatus.INDEX_RENAMED, "R"),
    (FileStatus.INDEX_TYPECHANGE, "T"),
)

# 第二列 (工作区).
_WORKTREE_MARKS: tuple[tuple[FileStatus, str], ...] = (
    (FileStatus.WT_NEW, "?"),
    (FileStatus.WT_MODIFIED, "M"),
    (FileStatus.WT_DELETED, "D"),
    (FileStatus.WT_RENAMED, "R"),
    (FileStatus.WT_TYPECHANGE, "T"),
)

# log 带路径过滤时最多翻多少个提交. 每翻一个都要 diff 一次它和父提交, 没有上限的话
# 一个大仓库上的 "这个文件最近改过什么" 会走完整段历史.
_LOG_SCAN_LIMIT = 2000


class Pygit2GitQueries(GitQueries):
    def status(self, root: str) -> tuple[GitStatusEntry, ...]:
        repository = self._repository(root)
        try:
            raw = repository.status()
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"读取 git 状态失败: {exc}") from exc
        entries = [
            GitStatusEntry(
                path=path,
                index=_mark(flags, _INDEX_MARKS),
                worktree=_mark(flags, _WORKTREE_MARKS),
            )
            for path, flags in sorted(raw.items())
            if not flags & FileStatus.IGNORED
        ]
        return tuple(entries)

    def diff(
        self,
        root: str,
        *,
        staged: bool = False,
        paths: Sequence[str] = (),
        context_lines: int = 3,
    ) -> GitPatch:
        repository = self._repository(root)
        try:
            if staged:
                # index vs HEAD. 没有 HEAD (刚 init 还没提交) 时下面会抛, 归成查询失败.
                diff = repository.diff("HEAD", cached=True, context_lines=context_lines)
            else:
                # workdir vs index.
                diff = repository.diff(context_lines=context_lines)
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"生成 diff 失败: {exc}") from exc
        return _patch_of(diff, paths)

    def log(
        self,
        root: str,
        *,
        revision: str | None = None,
        max_count: int = 20,
        paths: Sequence[str] = (),
    ) -> tuple[GitCommit, ...]:
        repository = self._repository(root)
        start = self._resolve_commit(repository, revision or "HEAD")
        wanted = {path.strip("/") for path in paths if path.strip("/")}
        commits: list[GitCommit] = []
        scanned = 0
        try:
            for commit in repository.walk(
                start.id, SortMode.TOPOLOGICAL | SortMode.TIME
            ):
                scanned += 1
                if wanted and not _touches(repository, commit, wanted):
                    if scanned >= _LOG_SCAN_LIMIT:
                        break
                    continue
                commits.append(_commit_of(commit))
                if len(commits) >= max_count or scanned >= _LOG_SCAN_LIMIT:
                    break
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"遍历提交历史失败: {exc}") from exc
        return tuple(commits)

    def show(self, root: str, revision: str) -> tuple[GitCommit, GitPatch]:
        repository = self._repository(root)
        commit = self._resolve_commit(repository, revision)
        try:
            if commit.parents:
                diff = repository.diff(commit.parents[0], commit)
            else:
                # 首个提交没有父提交: 和空树比, 于是整个提交显示成新增.
                diff = commit.tree.diff_to_tree(swap=True)
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"生成提交补丁失败: {exc}") from exc
        return (_commit_of(commit), _patch_of(diff, ()))

    def blame(
        self,
        root: str,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> tuple[GitBlameHunk, ...]:
        repository = self._repository(root)
        try:
            blame = repository.blame(path, min_line=start_line, max_line=end_line)
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"blame {path} 失败: {exc}") from exc
        return tuple(
            GitBlameHunk(
                start_line=hunk.final_start_line_number,
                line_count=hunk.lines_in_hunk,
                short_id=str(hunk.final_commit_id)[:8],
                author=_name_of(hunk.final_committer),  # type: ignore[arg-type]
            )
            for hunk in blame
        )

    def branches(
        self, root: str, *, include_remote: bool = False
    ) -> tuple[GitBranch, ...]:
        repository = self._repository(root)
        try:
            head = repository.head.shorthand if not repository.head_is_unborn else ""
        except _LIBGIT2_ERRORS:
            head = ""
        found: list[GitBranch] = []
        try:
            for name in sorted(repository.branches.local):
                found.append(_branch_of(repository, name, is_remote=False, head=head))
            if include_remote:
                for name in sorted(repository.branches.remote):
                    found.append(
                        _branch_of(repository, name, is_remote=True, head=head)
                    )
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"列出分支失败: {exc}") from exc
        return tuple(found)

    def remotes(self, root: str) -> tuple[GitRemote, ...]:
        repository = self._repository(root)
        try:
            return tuple(
                GitRemote(name=remote.name or "", url=remote.url or "")
                for remote in repository.remotes
            )
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"读取远端配置失败: {exc}") from exc

    def stashes(self, root: str) -> tuple[GitStash, ...]:
        repository = self._repository(root)
        try:
            raw = repository.listall_stashes()
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"读取 stash 失败: {exc}") from exc
        return tuple(
            GitStash(
                index=position,
                message=stash.message,
                short_id=str(stash.commit_id)[:8],
            )
            for position, stash in enumerate(raw)
        )

    # ---- 内部 ----

    @staticmethod
    def _repository(root: str) -> pygit2.Repository:
        try:
            discovered = pygit2.discover_repository(root)
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"{root} 不在一个 git 仓库里") from exc
        if discovered is None:
            raise GitQueryError(f"{root} 不在一个 git 仓库里")
        try:
            return pygit2.Repository(discovered)
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"打开 git 仓库失败: {exc}") from exc

    @staticmethod
    def _resolve_commit(repository: pygit2.Repository, revision: str) -> pygit2.Commit:
        try:
            resolved = repository.revparse_single(revision)
        except _LIBGIT2_ERRORS as exc:
            raise GitQueryError(f"解析不了版本 {revision!r}: {exc}") from exc
        commit = _peel_to_commit(resolved)
        if commit is None:
            raise GitUnsupported(f"{revision!r} 不是一个提交; git_read 只处理提交")
        return commit


def _peel_to_commit(obj: object) -> pygit2.Commit | None:
    """标签指向提交, 提交就是提交, 树和 blob 不是."""
    if isinstance(obj, pygit2.Commit):
        return obj
    if isinstance(obj, pygit2.Tag):
        target = obj.peel(pygit2.Commit)
        return target if isinstance(target, pygit2.Commit) else None
    return None


def _mark(flags: int, table: tuple[tuple[FileStatus, str], ...]) -> str:
    for flag, char in table:
        if flags & flag:
            return char
    return " "


def _patch_of(diff: pygit2.Diff, paths: Sequence[str]) -> GitPatch:
    wanted = {path.strip("/") for path in paths if path.strip("/")}
    if not wanted:
        stats = diff.stats
        return GitPatch(
            text=diff.patch or "",
            stats=GitDiffStats(
                files_changed=stats.files_changed,
                insertions=stats.insertions,
                deletions=stats.deletions,
            ),
        )
    # 路径过滤: libgit2 的 diff 不收 pathspec, 所以在这里按 delta 挑.
    #
    # 迭代 Diff 得到的是 `Patch | None` —— libgit2 对某些 delta (子模块, 冲突项) 不产出
    # patch. 那些 delta 无法按路径判定, 也没有正文可拼, 跳过.
    kept = [
        patch for patch in diff if patch is not None and _within(patch.delta, wanted)
    ]
    insertions = sum(patch.line_stats[1] for patch in kept)
    deletions = sum(patch.line_stats[2] for patch in kept)
    return GitPatch(
        text="".join(patch.text or "" for patch in kept),
        stats=GitDiffStats(
            files_changed=len(kept), insertions=insertions, deletions=deletions
        ),
    )


def _within(delta: pygit2.DiffDelta, wanted: set[str]) -> bool:
    candidates = {delta.new_file.path, delta.old_file.path}
    return any(
        path == prefix or path.startswith(f"{prefix}/")
        for path in candidates
        for prefix in wanted
    )


def _touches(
    repository: pygit2.Repository, commit: pygit2.Commit, wanted: set[str]
) -> bool:
    if not commit.parents:
        diff = commit.tree.diff_to_tree(swap=True)
    else:
        diff = repository.diff(commit.parents[0], commit)
    return any(_within(delta, wanted) for delta in diff.deltas)


def _commit_of(commit: pygit2.Commit) -> GitCommit:
    return GitCommit(
        short_id=commit.short_id,
        author=_name_of(commit.author),
        committed_at=_when(commit.commit_time, commit.commit_time_offset),
        summary=commit.message.strip().splitlines()[0]
        if commit.message.strip()
        else "",
    )


def _branch_of(
    repository: pygit2.Repository, name: str, *, is_remote: bool, head: str
) -> GitBranch:
    collection = repository.branches.remote if is_remote else repository.branches.local
    branch = collection[name]
    target = branch.target
    return GitBranch(
        name=name,
        is_head=(not is_remote and name == head),
        is_remote=is_remote,
        short_id=str(target)[:8],
    )


def _name_of(signature: pygit2.Signature) -> str:
    try:
        return signature.name or ""
    except (UnicodeDecodeError, ValueError):
        # 提交里的作者名可以是任意字节. 读不出来不该让整次查询失败.
        return "(读不出的作者名)"


def _when(seconds: int, offset_minutes: int) -> str:
    zone = timezone(timedelta(minutes=offset_minutes))
    return datetime.fromtimestamp(seconds, tz=UTC).astimezone(zone).isoformat()
