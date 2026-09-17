"""装权限上下文 (ADR-0055 决策 2; 对应 permissionSetup.ts).

五个来源的三张表合起来, 不去重, 每条记来源; defaultMode 命令行优先; 额外目录展开成
绝对路径. bypass 的可用性是启动期事实: 参数 + 一次性确认 + 设置里没锁. auto 的可用性
也是 (ADR-0058 决策 1): 有分类器可接 + 设置里没锁.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from forgecli.common.observability.log import get_log
from forgecli.core.permissions import (
    AdditionalWorkingDirectory,
    ModeUnavailableError,
    PermissionBehavior,
    PermissionMode,
    PermissionRuleSource,
    ToolPermissionContext,
    parse_permission_mode,
)
from forgecli.security.auto_mode import (
    is_auto_mode_active,
    restore_dangerous_permissions,
    strip_dangerous_permissions_for_auto_mode,
)
from forgecli.security.policy_rules import POLICY_DENY_RULES
from forgecli.security.settings_store import (
    PermissionsSection,
    project_settings_file,
    read_permissions,
    user_settings_file,
)
from forgecli.security.shadowed_rule_detection import warn_shadowed_rules

__all__ = [
    "PermissionCliArgs",
    "get_initial_permission_context",
    "is_auto_mode_disabled",
    "is_bypass_permissions_mode_disabled",
    "transition_permission_mode",
]

_log = get_log(__name__)


class PermissionCliArgs:
    """命令行给的那几项. 没有的留空."""

    def __init__(
        self,
        *,
        mode: str = "",
        allowed_tools: tuple[str, ...] = (),
        disallowed_tools: tuple[str, ...] = (),
        additional_directories: tuple[str, ...] = (),
        bypass_requested: bool = False,
        avoid_prompts: bool = False,
    ) -> None:
        self.mode = mode
        self.allowed_tools = allowed_tools
        self.disallowed_tools = disallowed_tools
        self.additional_directories = additional_directories
        self.bypass_requested = bypass_requested
        self.avoid_prompts = avoid_prompts


def is_bypass_permissions_mode_disabled(project_id: str | None) -> bool:
    """设置里锁了 bypass 就不给 (对应 bypassPermissionsKillswitch.ts)."""
    if read_permissions(user_settings_file()).disable_bypass_permissions_mode:
        return True
    if project_id is not None:
        section = read_permissions(project_settings_file(project_id))
        if section.disable_bypass_permissions_mode:
            return True
    return False


def is_auto_mode_disabled(project_id: str | None) -> bool:
    """设置里 permissions.disableAutoMode 为 "disable" 就不给 auto (ADR-0058 决策 1)."""
    if read_permissions(user_settings_file()).disable_auto_mode:
        return True
    if project_id is None:
        return False
    return read_permissions(project_settings_file(project_id)).disable_auto_mode


def _expand_directory(raw: str, cwd: str) -> str:
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return str(Path(expanded).resolve())


def get_initial_permission_context(
    *,
    project_id: str | None,
    cwd: str,
    cli: PermissionCliArgs,
    bypass_consented: bool,
    classifier_wired: bool = False,
) -> ToolPermissionContext:
    """`classifier_wired`: 调用方能不能给 auto 模式接上分类器 (有模型网关). 没有就不
    列 auto 这一档."""
    user = read_permissions(user_settings_file())
    project = PermissionsSection()
    if project_id is not None:
        project = read_permissions(project_settings_file(project_id))

    allow: dict[PermissionRuleSource, tuple[str, ...]] = {}
    deny: dict[PermissionRuleSource, tuple[str, ...]] = {}
    ask: dict[PermissionRuleSource, tuple[str, ...]] = {}
    deny[PermissionRuleSource.POLICY_SETTINGS] = POLICY_DENY_RULES
    _fill(allow, deny, ask, PermissionRuleSource.USER_SETTINGS, user)
    _fill(allow, deny, ask, PermissionRuleSource.PROJECT_SETTINGS, project)
    if cli.allowed_tools:
        allow[PermissionRuleSource.CLI_ARG] = cli.allowed_tools
    if cli.disallowed_tools:
        deny[PermissionRuleSource.CLI_ARG] = cli.disallowed_tools

    directories: list[AdditionalWorkingDirectory] = []
    for raw in user.additional_directories:
        directories.append(
            AdditionalWorkingDirectory(
                path=_expand_directory(raw, cwd),
                source=PermissionRuleSource.USER_SETTINGS,
            )
        )
    for raw in project.additional_directories:
        directories.append(
            AdditionalWorkingDirectory(
                path=_expand_directory(raw, cwd),
                source=PermissionRuleSource.PROJECT_SETTINGS,
            )
        )
    for raw in cli.additional_directories:
        directories.append(
            AdditionalWorkingDirectory(
                path=_expand_directory(raw, cwd), source=PermissionRuleSource.CLI_ARG
            )
        )

    bypass_available = (
        cli.bypass_requested
        and bypass_consented
        and not is_bypass_permissions_mode_disabled(project_id)
    )
    auto_available = classifier_wired and not is_auto_mode_disabled(project_id)
    mode = _initial_mode(cli.mode, project.default_mode, user.default_mode)
    if mode is PermissionMode.BYPASS_PERMISSIONS and not bypass_available:
        _log.warning("permissions.bypass_unavailable", requested=mode.value)
        mode = PermissionMode.DEFAULT
    if mode is PermissionMode.AUTO and not auto_available:
        _log.warning("permissions.auto_unavailable", requested=mode.value)
        mode = PermissionMode.DEFAULT

    context = ToolPermissionContext(
        mode=mode,
        additional_working_directories=tuple(directories),
        always_allow_rules=allow,
        always_deny_rules=deny,
        always_ask_rules=ask,
        is_bypass_permissions_mode_available=bypass_available,
        is_auto_mode_available=auto_available,
        should_avoid_permission_prompts=cli.avoid_prompts,
    )
    warn_shadowed_rules(context)
    if mode is PermissionMode.AUTO:
        # defaultMode 就是 auto 时也要剥掉危险规则, 与运行中切过去的一样.
        context = strip_dangerous_permissions_for_auto_mode(context)
    return context


def _fill(
    allow: dict[PermissionRuleSource, tuple[str, ...]],
    deny: dict[PermissionRuleSource, tuple[str, ...]],
    ask: dict[PermissionRuleSource, tuple[str, ...]],
    source: PermissionRuleSource,
    section: PermissionsSection,
) -> None:
    if section.rules(PermissionBehavior.ALLOW):
        allow[source] = section.rules(PermissionBehavior.ALLOW)
    if section.rules(PermissionBehavior.DENY):
        deny[source] = section.rules(PermissionBehavior.DENY)
    if section.rules(PermissionBehavior.ASK):
        ask[source] = section.rules(PermissionBehavior.ASK)


def _initial_mode(
    cli_mode: str, project_default: str, user_default: str
) -> PermissionMode:
    for raw in (cli_mode, project_default, user_default):
        if not raw:
            continue
        try:
            return parse_permission_mode(raw)
        except ValueError:
            _log.warning("permissions.unknown_mode", raw=raw)
    return PermissionMode.DEFAULT


def transition_permission_mode(
    context: ToolPermissionContext, new_mode: PermissionMode
) -> ToolPermissionContext:
    """换模式 (对应 transitionPermissionMode). 进 plan 记 pre_plan_mode, 出 plan 清掉;
    进 auto 剥危险规则, 出 auto 放回; bypass / auto 没放开就拒."""
    if (
        new_mode is PermissionMode.BYPASS_PERMISSIONS
        and not context.is_bypass_permissions_mode_available
    ):
        raise ModeUnavailableError(
            "Bypass Permissions 没有在启动时放开: 带 --allow-full-access 重新启动 "
            "forge."
        )
    if new_mode is PermissionMode.AUTO and not context.is_auto_mode_available:
        raise ModeUnavailableError(
            "Auto 没有放开: 设置里 permissions.disableAutoMode 锁了它, 或者这个入口"
            "没有接分类器."
        )
    if new_mode is context.mode:
        return context
    # 从 auto 进 plan 时分类器继续管 plan 下的 ask, 所以剥掉的规则不放回; 判据是
    # "换完之后分类器还在不在", 而不是 mode 字面上是不是 auto.
    from_classifier = is_auto_mode_active(context)
    to_classifier = new_mode is PermissionMode.AUTO or (
        new_mode is PermissionMode.PLAN and context.mode is PermissionMode.AUTO
    )
    updated = context
    if to_classifier and not from_classifier:
        updated = strip_dangerous_permissions_for_auto_mode(updated)
    elif from_classifier and not to_classifier:
        updated = restore_dangerous_permissions(updated)
    if new_mode is PermissionMode.PLAN:
        return replace(updated, mode=new_mode, pre_plan_mode=context.mode)
    if context.mode is PermissionMode.PLAN:
        return replace(updated, mode=new_mode, pre_plan_mode=None)
    return replace(updated, mode=new_mode)
