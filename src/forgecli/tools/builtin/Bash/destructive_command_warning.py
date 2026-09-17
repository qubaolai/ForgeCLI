"""审批卡片上的提醒 (对应 BashTool/destructiveCommandWarning.ts). 不参与裁决."""

from __future__ import annotations

import re

__all__ = ["get_destructive_command_warning"]

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+|-[a-zA-Z]*f[a-zA-Z]*\s+)"),
        "rm with -r/-f permanently deletes files",
    ),
    (
        re.compile(r"\bgit\s+push\b.*(--force|-f\b)"),
        "git push --force rewrites remote history",
    ),
    (
        re.compile(r"\bgit\s+reset\s+--hard"),
        "git reset --hard discards uncommitted changes",
    ),
    (
        re.compile(r"\bgit\s+checkout\s+(--\s+)?\."),
        "git checkout . discards working tree changes",
    ),
    (
        re.compile(r"\bgit\s+clean\b.*-[a-zA-Z]*f"),
        "git clean -f deletes untracked files",
    ),
    (
        re.compile(r"\bgit\s+branch\s+-D\b"),
        "git branch -D deletes a branch without merge checks",
    ),
    (
        re.compile(r"\bgit\s+stash\s+(drop|clear)\b"),
        "git stash drop/clear discards stashed changes",
    ),
    (
        re.compile(r"\bdrop\s+(table|database)\b", re.IGNORECASE),
        "DROP TABLE/DATABASE is irreversible",
    ),
    (re.compile(r"\btruncate\b"), "truncate discards data"),
    (re.compile(r">\s*/dev/sd"), "writing to a block device destroys data"),
    (
        re.compile(r"\bmkfs\b|\bdd\s+.*of=/dev/"),
        "formatting or writing a device destroys data",
    ),
    (re.compile(r"\bchmod\s+(-R\s+)?777\b"), "chmod 777 makes files world-writable"),
)


def get_destructive_command_warning(command: str) -> str | None:
    for pattern, warning in _PATTERNS:
        if pattern.search(command):
            return warning
    return None
