"""只读 git 查询的端口与结果词汇 (ADR-0040 决策 4.4).

`git_read` 原先是"把子命令和参数拼成 argv, 交给执行器起一个 git 进程". 那条路上有两样
东西必须自己守:

- **一张 CLI 参数白名单.** git 的只读命令里藏着执行入口: `git diff --ext-diff` 与
  `--textconv` 会跑外部程序, `--output=F` 会写文件, `git -c core.pager=...` 能指定任意
  命令. 挡住它们的办法是穷举允许的选项, 而那张表按定义永远不完整 —— git 每加一个选项,
  它就旧一点 (ADR-0040 决策 7).
- **一次 SPAWN_PROCESS.** 为了读一行 `git status`, 工具要声明"我会起子进程"这个能力上界.

libgit2 (经 pygit2) 把这两样都拿掉了: 它是进程内的库调用, 没有 argv 可拼, 也不实现
textconv / ext-diff / pager 这些外部程序入口 —— 那些能力压根不存在, 不需要被挡住.

于是这里的参数是**结构化的**: `staged=True` 而不是 `--cached`, `max_count=20` 而不是
`-n 20`. 模型给不出"一个选项", 只能给出这些字段, 而每个字段的取值范围由 schema 说了算.

## 为什么是端口而不是直接调 pygit2

pygit2 是 C 扩展, 它的 Repository / Diff / Commit 都是原生对象. 让 application 直接
认识它们, 等于把 libgit2 的对象模型焊进工具层 (ADR-0040 决策 3, ADR-0028 规则 A1).
下面这些值对象是纯数据, 换掉 pygit2 只需要换 infrastructure 里那一个实现.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from forgecli.shared.errors import ForgeError

__all__ = [
    "GitBlameHunk",
    "GitBranch",
    "GitCommit",
    "GitDiffStats",
    "GitPatch",
    "GitQueries",
    "GitQueryError",
    "GitRemote",
    "GitStash",
    "GitStatusEntry",
    "GitUnsupported",
]


class GitQueryError(ForgeError):
    """查询失败; message 会原样回给模型, 因此必须是一句人话."""


class GitUnsupported(GitQueryError):
    """libgit2 覆盖不到, 或者语义与 git CLI 差得太远的查询.

    单独一个类型, 是为了让"我们做不到"和"这个仓库有问题"在结果里分得开. 前者的正确
    出路是 `shell_run` 加正常的安全裁决, 后者是修仓库.
    """


@dataclass(frozen=True)
class GitStatusEntry:
    """一条工作区状态.

    index / worktree 各是一个字符, 与 `git status --short` 的两列同义.
    """

    path: str
    index: str
    worktree: str


@dataclass(frozen=True)
class GitDiffStats:
    files_changed: int
    insertions: int
    deletions: int


@dataclass(frozen=True)
class GitPatch:
    text: str
    stats: GitDiffStats


@dataclass(frozen=True)
class GitCommit:
    short_id: str
    author: str
    committed_at: str  # ISO 8601, 带时区
    summary: str


@dataclass(frozen=True)
class GitBlameHunk:
    start_line: int
    line_count: int
    short_id: str
    author: str


@dataclass(frozen=True)
class GitBranch:
    name: str
    is_head: bool
    is_remote: bool
    short_id: str


@dataclass(frozen=True)
class GitRemote:
    name: str
    url: str


@dataclass(frozen=True)
class GitStash:
    index: int
    message: str
    short_id: str


class GitQueries(ABC):
    """一个工作区上的只读 git 查询. 每个方法要么返回结果, 要么抛 GitQueryError."""

    @abstractmethod
    def status(self, root: str) -> tuple[GitStatusEntry, ...]:
        """工作区与暂存区相对 HEAD 的状态."""

    @abstractmethod
    def diff(
        self,
        root: str,
        *,
        staged: bool = False,
        paths: Sequence[str] = (),
        context_lines: int = 3,
    ) -> GitPatch:
        """未暂存改动 (staged=False) 或已暂存改动 (staged=True) 的补丁."""

    @abstractmethod
    def log(
        self,
        root: str,
        *,
        revision: str | None = None,
        max_count: int = 20,
        paths: Sequence[str] = (),
    ) -> tuple[GitCommit, ...]:
        """从 revision (缺省 HEAD) 往回走的提交; paths 非空时只留改动过它们的提交."""

    @abstractmethod
    def show(self, root: str, revision: str) -> tuple[GitCommit, GitPatch]:
        """一个提交的元信息与它相对首个父提交的补丁."""

    @abstractmethod
    def blame(
        self,
        root: str,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> tuple[GitBlameHunk, ...]:
        """一个文件每一段的最后改动者."""

    @abstractmethod
    def branches(
        self, root: str, *, include_remote: bool = False
    ) -> tuple[GitBranch, ...]:
        """本地分支; include_remote 时并上远端跟踪分支."""

    @abstractmethod
    def remotes(self, root: str) -> tuple[GitRemote, ...]:
        """远端名字与 URL. 只读本地配置, 不联网."""

    @abstractmethod
    def stashes(self, root: str) -> tuple[GitStash, ...]:
        """stash 栈, 索引 0 是栈顶."""
