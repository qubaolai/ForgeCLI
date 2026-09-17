"""施加一次 PermissionUpdate (对应 PermissionUpdate.ts).

目标是 user_settings / project_settings / session: 前两者写回设置文件, 后者只改内存.
返回新的上下文, 调用方负责放回 AppState.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from forgecli.core.permissions import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionRuleSource,
    PermissionUpdate,
    PermissionUpdateDestination,
    PermissionUpdateType,
    ToolPermissionContext,
)
from forgecli.security.permission_rule_parser import permission_rule_value_to_string
from forgecli.security.settings_store import (
    PermissionsSection,
    read_permissions,
    write_permissions,
)

__all__ = ["PermissionUpdateApplier"]

_SOURCE_OF_DESTINATION: dict[PermissionUpdateDestination, PermissionRuleSource] = {
    PermissionUpdateDestination.USER_SETTINGS: PermissionRuleSource.USER_SETTINGS,
    PermissionUpdateDestination.PROJECT_SETTINGS: PermissionRuleSource.PROJECT_SETTINGS,
    PermissionUpdateDestination.SESSION: PermissionRuleSource.SESSION,
}


class PermissionUpdateApplier:
    def __init__(self, *, user_file: Path, project_file: Path | None) -> None:
        self._user_file = user_file
        self._project_file = project_file

    def apply(
        self, update: PermissionUpdate, context: ToolPermissionContext
    ) -> ToolPermissionContext:
        source = _SOURCE_OF_DESTINATION[update.destination]
        if update.type is PermissionUpdateType.SET_MODE:
            if update.mode is None:
                return context
            return replace(context, mode=update.mode)
        if update.type in (
            PermissionUpdateType.ADD_DIRECTORIES,
            PermissionUpdateType.REMOVE_DIRECTORIES,
        ):
            return self._apply_directories(update, context, source)
        return self._apply_rules(update, context, source)

    def _apply_rules(
        self,
        update: PermissionUpdate,
        context: ToolPermissionContext,
        source: PermissionRuleSource,
    ) -> ToolPermissionContext:
        behavior = update.behavior
        if behavior is None:
            return context
        texts = tuple(permission_rule_value_to_string(rule) for rule in update.rules)
        table = _table_of(context, behavior)
        current = list(table.get(source, ()))
        if update.type is PermissionUpdateType.ADD_RULES:
            for text in texts:
                if text not in current:
                    current.append(text)
        elif update.type is PermissionUpdateType.REPLACE_RULES:
            current = list(texts)
        elif update.type is PermissionUpdateType.REMOVE_RULES:
            current = [text for text in current if text not in texts]
        new_table = dict(table)
        new_table[source] = tuple(current)
        updated = _with_table(context, behavior, new_table)
        self._persist(update.destination, updated)
        return updated

    def _apply_directories(
        self,
        update: PermissionUpdate,
        context: ToolPermissionContext,
        source: PermissionRuleSource,
    ) -> ToolPermissionContext:
        current = list(context.additional_working_directories)
        if update.type is PermissionUpdateType.ADD_DIRECTORIES:
            known = {item.path for item in current}
            for directory in update.directories:
                if directory not in known:
                    current.append(
                        AdditionalWorkingDirectory(path=directory, source=source)
                    )
        else:
            current = [
                item for item in current if item.path not in set(update.directories)
            ]
        updated = replace(context, additional_working_directories=tuple(current))
        self._persist(update.destination, updated)
        return updated

    def _persist(
        self, destination: PermissionUpdateDestination, context: ToolPermissionContext
    ) -> None:
        if destination is PermissionUpdateDestination.SESSION:
            return
        if destination is PermissionUpdateDestination.USER_SETTINGS:
            path = self._user_file
            source = PermissionRuleSource.USER_SETTINGS
        else:
            if self._project_file is None:
                return
            path = self._project_file
            source = PermissionRuleSource.PROJECT_SETTINGS
        existing = read_permissions(path)
        directories = tuple(
            item.path
            for item in context.additional_working_directories
            if item.source is source
        )
        write_permissions(
            path,
            PermissionsSection(
                allow=context.always_allow_rules.get(source, ()),
                deny=context.always_deny_rules.get(source, ()),
                ask=context.always_ask_rules.get(source, ()),
                additional_directories=directories,
                default_mode=existing.default_mode,
                disable_bypass_permissions_mode=existing.disable_bypass_permissions_mode,
            ),
        )


def _table_of(
    context: ToolPermissionContext, behavior: PermissionBehavior
) -> dict[PermissionRuleSource, tuple[str, ...]]:
    if behavior is PermissionBehavior.ALLOW:
        return dict(context.always_allow_rules)
    if behavior is PermissionBehavior.DENY:
        return dict(context.always_deny_rules)
    return dict(context.always_ask_rules)


def _with_table(
    context: ToolPermissionContext,
    behavior: PermissionBehavior,
    table: dict[PermissionRuleSource, tuple[str, ...]],
) -> ToolPermissionContext:
    if behavior is PermissionBehavior.ALLOW:
        return replace(context, always_allow_rules=table)
    if behavior is PermissionBehavior.DENY:
        return replace(context, always_deny_rules=table)
    return replace(context, always_ask_rules=table)
