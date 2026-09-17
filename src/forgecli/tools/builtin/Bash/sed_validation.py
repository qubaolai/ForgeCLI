"""sed 的约束 (对应 BashTool/sedValidation.ts 的要点).

`sed -i` 改文件, 脚本里的 `w` / `e` 命令能写文件或执行命令. 这些在模式放行之前挡下,
让它们走 ask; 只有 accept_edits 的文件系统命令放行 (mode_validation) 能放过
`sed -i` 且目标在工作目录内的情形. 判据 fail closed: 拿不准的脚本按有写 / 执行算.
"""

from __future__ import annotations

import re

from forgecli.core.permissions import PermissionResult, ask, other_reason, passthrough
from forgecli.security.shell.command_plan import CommandUnit

__all__ = ["check_sed_constraints", "is_in_place"]

# `w file` / `W file`: 写文件; `e` / `e cmd` / `s///e`: 执行.
_WRITE = re.compile(r"(?<![\w\\])[wW]\s+\S")
_EXEC = re.compile(r"(?<![\w\\])e(\s+\S|\s*$|\s*;)|/[gIp]*e[gIp]*(\s|$|;)")


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
