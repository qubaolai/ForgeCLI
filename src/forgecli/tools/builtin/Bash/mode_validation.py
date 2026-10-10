"""模式相关的放行 (对应 BashTool/modeValidation.ts).

accept_edits 下, 只碰工作目录内路径的文件系统命令 (mkdir / touch / rm / rmdir / mv /
cp / sed) 自动放行.
"""

from __future__ import annotations

from forgecli.core.permissions import (
    PermissionMode,
    PermissionResult,
    allow,
    mode_reason,
    passthrough,
)
from forgecli.core.tool.tool import ToolUseContext
from forgecli.security.filesystem import expand_path, path_in_allowed_working_path
from forgecli.security.shell.command_plan import CommandUnit

__all__ = ["ACCEPT_EDITS_ALLOWED_COMMANDS", "check_permission_mode"]

ACCEPT_EDITS_ALLOWED_COMMANDS: tuple[str, ...] = (
    "mkdir",
    "touch",
    "rm",
    "rmdir",
    "mv",
    "cp",
    "sed",
)


def _paths_inside(unit: CommandUnit, context: ToolUseContext) -> bool:
    """检查一个命令的所有路径参数是否都位于允许的工作目录范围内"""
    for arg in unit.argv:
        if arg.startswith("-"):
            # - 开头的参数 不是路径
            continue
        if unit.name == "sed" and not _looks_like_path(arg):
            continue
        if "$" in arg or "*" in arg:
            # 可能是变量 无法静态检测
            return False
        absolute = expand_path(arg, context.cwd)
        if not path_in_allowed_working_path(absolute, context):
            return False
    return True


def _looks_like_path(arg: str) -> bool:
    return "/" in arg or "." in arg


def check_permission_mode(
    unit: CommandUnit, context: ToolUseContext
) -> PermissionResult:
    permission = context.get_app_state().tool_permission_context
    if permission.mode is not PermissionMode.ACCEPT_EDITS:
        return passthrough(f"No mode-specific handling in {permission.mode.value}")
    if unit.name not in ACCEPT_EDITS_ALLOWED_COMMANDS:
        return passthrough(f"No mode-specific handling for {unit.name}")
    # 路径检查
    if not _paths_inside(unit, context):
        return passthrough(f"{unit.name} touches paths outside the working directories")
    return allow(reason=mode_reason(PermissionMode.ACCEPT_EDITS))
