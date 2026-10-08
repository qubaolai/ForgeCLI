"""会话开始时的 git 快照 (ADR-0056 决策 7).

只在会话刷新点读一次, 进 system prompt 的会话段. 读不到 (不是仓库, 没装 git, 超时) 就
没有这一节, 不抛错: 它是辅助信息, 一个坏掉的 git 不该让会话起不来.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from forgecli.prompt.session_facts import GitSnapshot

__all__ = ["read_git_snapshot"]

_TIMEOUT_SECONDS = 5


def _git(cwd: str, *args: str) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - 固定的 git 子命令, 参数不来自用户
            ["git", "--no-optional-locks", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def read_git_snapshot(cwd: str) -> GitSnapshot | None:
    if not (Path(cwd) / ".git").exists():
        return None
    branch = _git(cwd, "branch", "--show-current")
    if branch is None:
        return None
    main_branch = _default_branch(cwd)
    status = _git(cwd, "status", "--short") or ""
    log = _git(cwd, "log", "--oneline", "-n", "5") or ""
    user = _git(cwd, "config", "user.name") or ""
    return GitSnapshot(
        branch=branch or "(detached)",
        main_branch=main_branch,
        user_name=user,
        status=status,
        recent_commits=log,
    )


def _default_branch(cwd: str) -> str:
    """origin 的 HEAD 指向哪条; 没有远端时猜 main / master."""
    head = _git(cwd, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head:
        return head.removeprefix("origin/")
    for candidate in ("main", "master"):
        if _git(cwd, "rev-parse", "--verify", "--quiet", candidate) is not None:
            return candidate
    return "main"
