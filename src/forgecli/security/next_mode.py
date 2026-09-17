"""Tab 循环的下一档 (对应 getNextPermissionMode.ts).

default -> accept_edits -> plan -> (bypass_permissions, 若可用) -> (auto, 若可用)
-> default. dont_ask 不在循环里: 它只给无人值守的入口.
"""

from __future__ import annotations

from forgecli.core.permissions import PermissionMode, ToolPermissionContext

__all__ = ["get_next_permission_mode"]


def get_next_permission_mode(context: ToolPermissionContext) -> PermissionMode:
    mode = context.mode
    if mode is PermissionMode.DEFAULT:
        return PermissionMode.ACCEPT_EDITS
    if mode is PermissionMode.ACCEPT_EDITS:
        return PermissionMode.PLAN
    if mode is PermissionMode.PLAN:
        if context.is_bypass_permissions_mode_available:
            return PermissionMode.BYPASS_PERMISSIONS
        if context.is_auto_mode_available:
            return PermissionMode.AUTO
        return PermissionMode.DEFAULT
    if mode is PermissionMode.BYPASS_PERMISSIONS:
        if context.is_auto_mode_available:
            return PermissionMode.AUTO
        return PermissionMode.DEFAULT
    return PermissionMode.DEFAULT
