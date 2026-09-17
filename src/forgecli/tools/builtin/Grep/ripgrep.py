"""Grep 的内部函数: 找 rg, 拼 argv, 翻译退出码."""

from __future__ import annotations

import shutil
from collections.abc import Mapping

__all__ = ["EXCLUDED_DIRECTORIES", "argv_for", "find_ripgrep"]

# 版本控制目录不搜.
EXCLUDED_DIRECTORIES: tuple[str, ...] = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")


def find_ripgrep(environment: Mapping[str, str]) -> str | None:
    return shutil.which("rg", path=environment.get("PATH", ""))


def argv_for(rg: str, input: Mapping[str, object], *, path: str) -> tuple[str, ...]:
    """按 GrepTool.ts 的顺序拼 rg 参数."""
    args: list[str] = [rg, "--hidden"]
    for directory in EXCLUDED_DIRECTORIES:
        args.extend(["--glob", f"!{directory}"])
    args.extend(["--max-columns", "500"])
    if input.get("multiline") is True:
        args.extend(["-U", "--multiline-dotall"])
    if input.get("-i") is True:
        args.append("-i")
    mode = input.get("output_mode", "files_with_matches")
    if mode == "files_with_matches":
        args.append("-l")
    elif mode == "count":
        args.append("-c")
    else:
        if input.get("-n", True) is not False:
            args.append("-n")
        context = _number(input.get("context"))
        context_c = _number(input.get("-C"))
        before = _number(input.get("-B"))
        after = _number(input.get("-A"))
        if context is not None:
            args.extend(["-C", str(context)])
        elif context_c is not None:
            args.extend(["-C", str(context_c)])
        else:
            if before is not None:
                args.extend(["-B", str(before)])
            if after is not None:
                args.extend(["-A", str(after)])
    pattern = str(input.get("pattern", ""))
    if pattern.startswith("-"):
        args.extend(["-e", pattern])
    else:
        args.append(pattern)
    file_type = input.get("type")
    if isinstance(file_type, str) and file_type:
        args.extend(["--type", file_type])
    glob = input.get("glob")
    if isinstance(glob, str) and glob:
        for item in glob.split():
            args.extend(["--glob", item])
    args.append(path)
    return tuple(args)


def _number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
