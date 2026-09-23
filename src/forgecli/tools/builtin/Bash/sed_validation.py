"""sed 的约束 (对应 BashTool/sedValidation.ts 的要点).

`sed -i` 改文件, 脚本里的 `w` / `e` 命令能写文件或执行命令. 这些在模式放行之前挡下,
让它们走 ask. 只读自动放行只接受已验证的行范围打印脚本.
"""

from __future__ import annotations

import re

from forgecli.core.permissions import PermissionResult, ask, other_reason, passthrough
from forgecli.security.shell.command_plan import CommandUnit

__all__ = ["check_sed_constraints", "is_in_place"]

# `w file` / `W file`: 写文件; `e` / `e cmd` / `s///e`: 执行.
_WRITE = re.compile(r"(?<![\w\\])[wW]\s+\S")
_EXEC = re.compile(r"(?<![\w\\])e(\s+\S|\s*$|\s*;)|/[gIp]*e[gIp]*(\s|$|;)")


def sed_read_paths(unit: CommandUnit) -> tuple[str, ...] | None:
    """Recognize sed -n '1,60p' and explicit -e forms without executing scripts."""
    quiet = False
    scripts: list[str] = []
    paths: list[str] = []
    args = iter(unit.argv)
    options = True
    for arg in args:
        if options and arg == "--":
            options = False
        elif options and arg in {"-n", "--quiet", "--silent"}:
            quiet = True
        elif options and arg == "-e":
            scripts.append(next(args, ""))
        elif options and arg.startswith("-") and arg != "-":
            return None
        elif not scripts:
            scripts.append(arg)
        else:
            paths.append(arg)
    if (
        not quiet
        or not scripts
        or any(
            not re.fullmatch(r"\s*(?:\d+|\$)?(?:\s*,\s*(?:\d+|\$))?\s*p\s*", script)
            for script in scripts
        )
    ):
        return None
    return tuple(paths)


def is_in_place(unit: CommandUnit) -> bool:
    for arg in unit.argv:
        if arg in ("-i", "--in-place") or arg.startswith("--in-place="):
            return True
        if arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:]:
            return True
    return False


def check_sed_constraints(unit: CommandUnit) -> PermissionResult:
    if unit.name != "sed":
        return passthrough("not sed")
    if is_in_place(unit):
        return ask(
            "sed -i modifies files in place and requires approval",
            other_reason("sed in-place edit"),
        )
    for arg in unit.argv:
        if arg.startswith("-"):
            continue
        if _WRITE.search(arg) or _EXEC.search(arg):
            return ask(
                "sed script contains a write (w) or execute (e) command and requires "
                "approval",
                other_reason("sed write or execute command"),
            )
    return passthrough("sed constraints satisfied")
