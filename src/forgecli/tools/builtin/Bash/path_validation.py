"""路径约束 (对应 BashTool/pathValidation.ts 的 checkPathConstraints).

`cd` 到工作目录之外, 重定向到工作目录之外, 目标含 `$VAR` 的重定向, `rm -rf` 到危险
路径 —— 一律 ask. 排在 deny / ask 规则之后: 显式规则优先.
"""

from __future__ import annotations

import os
import re

from forgecli.core.permissions import (
    PermissionResult,
    ask,
    passthrough,
    working_dir_reason,
)
from forgecli.core.tool.tool import ToolUseContext
from forgecli.security.filesystem import expand_path, path_in_allowed_working_path
from forgecli.security.shell.command_plan import CommandUnit

__all__ = ["check_path_constraints"]

_VARIABLE = re.compile(r"\$\{?[A-Za-z_]|%[A-Za-z_]+%")
_DANGEROUS_REMOVAL_ROOTS = (
    "/",
    "~",
    "$HOME",
    "/home",
    "/Users",
    "/etc",
    "/usr",
    "/var",
    "/bin",
    "/sbin",
    "/lib",
    "/opt",
    "/boot",
    "/System",
    "/Library",
    "*",
)


def _is_recursive_rm(unit: CommandUnit) -> bool:
    if unit.name not in ("rm", "rmdir"):
        return False
    for arg in unit.argv:
        if arg.startswith("-") and not arg.startswith("--") and "r" in arg[1:]:
            return True
        if arg in ("--recursive", "-R"):
            return True
    return False


def check_path_constraints(
    units: tuple[CommandUnit, ...], context: ToolUseContext
) -> PermissionResult:
    for unit in units:
        # 重定向目标.
        for target in unit.write_targets:
            if _VARIABLE.search(target):
                return ask(
                    f"Output redirection target '{target}' contains a shell variable "
                    "and cannot be validated",
                    working_dir_reason("redirect target with variable"),
                )
            if not path_in_allowed_working_path(
                expand_path(target, context.cwd), context
            ):
                return ask(
                    f"Output redirection to '{target}' is outside the working "
                    "directories and requires approval",
                    working_dir_reason("redirect outside working directory"),
                )
        # cd.
        if unit.name == "cd":
            target = unit.argv[0] if unit.argv else "~"
            if target == "-":
                return ask(
                    "cd - switches to an unknown previous directory",
                    working_dir_reason("cd to unknown directory"),
                )
            if _VARIABLE.search(target):
                return ask(
                    f"cd target '{target}' contains a shell variable and cannot be "
                    "validated",
                    working_dir_reason("cd target with variable"),
                )
            absolute = expand_path(target, context.cwd)
            if not path_in_allowed_working_path(absolute, context):
                return ask(
                    f"cd to '{target}' was blocked. Forge can only cd into the working "
                    "directories for this session.",
                    working_dir_reason("cd outside working directory"),
                )
        # rm -rf 到危险路径.
        if _is_recursive_rm(unit):
            for arg in unit.argv:
                if arg.startswith("-"):
                    continue
                normalized = (
                    os.path.normpath(os.path.expanduser(arg))
                    if not _VARIABLE.search(arg)
                    else arg
                )
                if (
                    arg in _DANGEROUS_REMOVAL_ROOTS
                    or normalized in _DANGEROUS_REMOVAL_ROOTS
                ):
                    return ask(
                        f"Recursive removal of '{arg}' requires manual approval",
                        working_dir_reason("dangerous removal path"),
                    )
    return passthrough("path constraints satisfied")
