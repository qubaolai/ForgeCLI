"""auto 模式的进出 (ADR-0058 决策 1 / 5; 对应 autoModeState.ts 与 permissionSetup.ts
里 stripDangerousPermissionsForAutoMode / restoreDangerousPermissions).

分类器只看得到没被规则放行的调用. 一条 `Bash(python:*)` 的 allow 规则在 auto 下等于
"任意代码不经分类器", 所以进 auto 时把这类规则从三张表的 allow 表里剥掉, 存进
`stripped_dangerous_rules`, 出 auto 原样放回. policy 来源没有 allow 规则, 不在此列.
"""

from __future__ import annotations

from dataclasses import replace

from forgecli.common.observability.log import get_log
from forgecli.core.permissions import (
    PermissionMode,
    PermissionRuleSource,
    ToolPermissionContext,
)
from forgecli.security.dangerous_patterns import is_dangerous_bash_permission
from forgecli.security.permission_rule_parser import permission_rule_value_from_string

__all__ = [
    "is_auto_mode_active",
    "restore_dangerous_permissions",
    "strip_dangerous_permissions_for_auto_mode",
]

_log = get_log(__name__)


def is_auto_mode_active(context: ToolPermissionContext) -> bool:
    """在 auto 里, 或者从 auto 进了 plan (plan 下的 ask 也交给分类器)."""
    if context.mode is PermissionMode.AUTO:
        return True
    return (
        context.mode is PermissionMode.PLAN
        and context.pre_plan_mode is PermissionMode.AUTO
    )


def strip_dangerous_permissions_for_auto_mode(
    context: ToolPermissionContext,
) -> ToolPermissionContext:
    """剥掉会绕过分类器的 allow 规则, 剥掉的按来源存起来. 已经剥过 (再进一次) 不重复."""
    if context.stripped_dangerous_rules is not None:
        return context
    kept: dict[PermissionRuleSource, tuple[str, ...]] = {}
    stripped: dict[PermissionRuleSource, tuple[str, ...]] = {}
    for source, rules in context.always_allow_rules.items():
        remaining: list[str] = []
        removed: list[str] = []
        for text in rules:
            if is_dangerous_bash_permission(permission_rule_value_from_string(text)):
                removed.append(text)
            else:
                remaining.append(text)
        if remaining:
            kept[source] = tuple(remaining)
        if removed:
            stripped[source] = tuple(removed)
            _log.info(
                "permissions.auto_mode_stripped_rules",
                source=source.value,
                rules=removed,
            )
    return replace(context, always_allow_rules=kept, stripped_dangerous_rules=stripped)


def restore_dangerous_permissions(
    context: ToolPermissionContext,
) -> ToolPermissionContext:
    """出 auto: 把剥掉的规则放回各自的来源, 清空暂存."""
    stash = context.stripped_dangerous_rules
    if stash is None:
        return context
    table: dict[PermissionRuleSource, tuple[str, ...]] = dict(
        context.always_allow_rules
    )
    for source, rules in stash.items():
        current = list(table.get(source, ()))
        for text in rules:
            if text not in current:
                current.append(text)
        table[source] = tuple(current)
    return replace(context, always_allow_rules=table, stripped_dangerous_rules=None)
