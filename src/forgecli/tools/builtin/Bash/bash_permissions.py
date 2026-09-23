"""Bash 自己的裁决 (ADR-0055 决策 5; 对应 BashTool/bashPermissions.ts).

    解析 -> too-complex 提前退出 -> 沙箱内自动放行 -> 整条命令精确匹配
    -> 逐子命令八步 -> 合并 (任一 deny 即 deny; 全部 allow 才 allow; 否则 ask 带建议)

逐子命令八步 (`bash_tool_check_permission`): 精确 deny / ask -> 前缀与通配 deny -> ask
-> 路径约束 -> 精确 allow -> 前缀 / 通配 allow -> sed 约束 -> 模式 -> 只读命令
-> passthrough.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass, replace

from forgecli.core.permissions import (
    PermissionBehavior,
    PermissionResult,
    PermissionRule,
    PermissionRuleValue,
    PermissionUpdate,
    PermissionUpdateDestination,
    PermissionUpdateType,
    ToolPermissionContext,
    allow,
    ask,
    deny,
    other_reason,
    passthrough,
    rule_reason,
    sandbox_override_reason,
    subcommand_results_reason,
)
from forgecli.core.tool.names import BASH_TOOL_NAME
from forgecli.core.tool.tool import ToolUseContext
from forgecli.security.permissions import (
    create_permission_request_message,
    get_allow_rules,
    get_ask_rules,
    get_deny_rules,
)
from forgecli.security.sandbox_rules import should_use_sandbox
from forgecli.security.shell.command_plan import (
    CommandPlan,
    CommandUnit,
    ParseStatus,
    ShellKind,
    UnitOrigin,
)
from forgecli.security.shell.parser import parse_command
from forgecli.security.shell_rule_matching import (
    match_wildcard_pattern,
    parse_permission_rule,
    suggestion_for_exact_command,
)
from forgecli.tools.builtin.Bash.directory_flow import directory_contexts
from forgecli.tools.builtin.Bash.mode_validation import check_permission_mode
from forgecli.tools.builtin.Bash.path_validation import check_path_constraints
from forgecli.tools.builtin.Bash.read_only_validation import unit_is_read_only
from forgecli.tools.builtin.Bash.sed_validation import check_sed_constraints

__all__ = [
    "bash_tool_check_permission",
    "bash_tool_has_permission",
    "command_text",
    "parse_for_permissions",
    "too_complex_reason",
]


@dataclass(frozen=True)
class _Matched:
    deny: PermissionRule | None
    ask: PermissionRule | None
    allow: PermissionRule | None


def parse_for_permissions(command: str, shell_kind: ShellKind, cwd: str) -> CommandPlan:
    return parse_command(command.strip(), shell_kind, cwd=cwd)


def too_complex_reason(plan: CommandPlan) -> str:
    """能静态分析的返回空串, 否则说明为什么不能 (对应 AST 的 too-complex)."""
    if plan.status is ParseStatus.PARSE_ERROR:
        return (
            "Command contains malformed syntax that cannot be parsed: "
            f"{plan.parse_error}"
        )
    if plan.status is not ParseStatus.OK:
        joined = ", ".join(plan.opaque_constructs) or plan.status.value
        return (
            f"Command contains constructs that cannot be statically analyzed: {joined}"
        )
    for unit in plan.units:
        if unit.origin in (UnitOrigin.SUBSTITUTION, UnitOrigin.PROCESS_SUBSTITUTION):
            return "Command contains command substitution ($(...), backticks or <(...))"
        if unit.opaque_reasons:
            return (
                "Command contains constructs that cannot be statically analyzed: "
                + ", ".join(unit.opaque_reasons)
            )
    return ""


def command_text(unit: CommandUnit, shell_kind: ShellKind) -> str:
    """规则匹配用的子命令文本: 命令名 + 参数, 去掉前置赋值与包装器."""
    parts = [unit.name, *unit.argv]
    if shell_kind is ShellKind.POSIX:
        return shlex.join(parts)
    return " ".join(parts)


def _rules_matching(
    permission: ToolPermissionContext, text: str, behavior: PermissionBehavior
) -> PermissionRule | None:
    if behavior is PermissionBehavior.DENY:
        rules = get_deny_rules(permission)
    elif behavior is PermissionBehavior.ASK:
        rules = get_ask_rules(permission)
    else:
        rules = get_allow_rules(permission)
    for rule in rules:
        value = rule.rule_value
        if value.tool_name != BASH_TOOL_NAME or value.rule_content is None:
            continue
        if _rule_hits(value.rule_content, text):
            return rule
    return None


def _rule_hits(content: str, text: str) -> bool:
    parsed = parse_permission_rule(content)
    if parsed.kind == "exact":
        return text == parsed.value
    if parsed.kind == "prefix":
        return text == parsed.value or text.startswith(parsed.value + " ")
    return match_wildcard_pattern(parsed.value, text)


def _exact_rule(
    permission: ToolPermissionContext, text: str, behavior: PermissionBehavior
) -> PermissionRule | None:
    if behavior is PermissionBehavior.DENY:
        rules = get_deny_rules(permission)
    elif behavior is PermissionBehavior.ASK:
        rules = get_ask_rules(permission)
    else:
        rules = get_allow_rules(permission)
    for rule in rules:
        value = rule.rule_value
        if value.tool_name == BASH_TOOL_NAME and value.rule_content == text:
            return rule
    return None


def _matching(permission: ToolPermissionContext, text: str) -> _Matched:
    return _Matched(
        deny=_rules_matching(permission, text, PermissionBehavior.DENY),
        ask=_rules_matching(permission, text, PermissionBehavior.ASK),
        allow=_rules_matching(permission, text, PermissionBehavior.ALLOW),
    )


def bash_tool_check_exact_match_permission(
    text: str, permission: ToolPermissionContext
) -> PermissionResult:
    """整条命令 (或一个子命令) 逐字相等的规则."""
    rule = _exact_rule(permission, text, PermissionBehavior.DENY)
    if rule is not None:
        return deny(
            f"Permission to use {BASH_TOOL_NAME} with command {text} has been denied.",
            rule_reason(rule),
        )
    rule = _exact_rule(permission, text, PermissionBehavior.ASK)
    if rule is not None:
        return ask(create_permission_request_message(BASH_TOOL_NAME), rule_reason(rule))
    rule = _exact_rule(permission, text, PermissionBehavior.ALLOW)
    if rule is not None:
        return allow(reason=rule_reason(rule))
    return passthrough(
        create_permission_request_message(BASH_TOOL_NAME),
        suggestions=suggestion_for_exact_command(BASH_TOOL_NAME, text),
    )


def bash_tool_check_permission(
    unit: CommandUnit,
    text: str,
    context: ToolUseContext,
    permission: ToolPermissionContext,
) -> PermissionResult:
    """一个子命令的八步."""
    # 1. 精确.
    exact = bash_tool_check_exact_match_permission(text, permission)
    if exact.is_deny or exact.is_ask:
        return exact
    # 2. 前缀与通配的 deny / ask.
    matched = _matching(permission, text)
    if matched.deny is not None:
        return deny(
            f"Permission to use {BASH_TOOL_NAME} with command {text} has been denied.",
            rule_reason(matched.deny),
        )
    if matched.ask is not None:
        return ask(
            create_permission_request_message(BASH_TOOL_NAME), rule_reason(matched.ask)
        )
    # 3. 路径约束.
    path_result = check_path_constraints((unit,), context)
    if not path_result.is_passthrough:
        return path_result
    # 4. 精确 allow.
    if exact.is_allow:
        return exact
    # 5. 前缀 / 通配 allow.
    if matched.allow is not None:
        return allow(reason=rule_reason(matched.allow))
    # 5b. sed 约束.
    sed_result = check_sed_constraints(unit)
    if not sed_result.is_passthrough:
        return sed_result
    # 6. 模式.
    mode_result = check_permission_mode(unit, context)
    if not mode_result.is_passthrough:
        return mode_result
    # 7. 只读命令.
    if unit_is_read_only(unit) == "":
        return allow(reason=other_reason("Read-only command is allowed"))
    # 8. passthrough, 附精确命令的建议.
    reason = other_reason("This command requires approval")
    return passthrough(
        create_permission_request_message(BASH_TOOL_NAME, reason),
        reason,
        suggestions=suggestion_for_exact_command(BASH_TOOL_NAME, text),
    )


def _check_early_exit_deny(
    text: str, permission: ToolPermissionContext
) -> PermissionResult | None:
    exact = bash_tool_check_exact_match_permission(text, permission)
    if exact.is_deny or exact.is_ask or exact.is_allow:
        return exact
    rule = _rules_matching(permission, text, PermissionBehavior.DENY)
    if rule is not None:
        return deny(
            f"Permission to use {BASH_TOOL_NAME} with command {text} has been denied.",
            rule_reason(rule),
        )
    return None


def _check_sandbox_auto_allow(
    texts: tuple[str, ...], permission: ToolPermissionContext
) -> PermissionResult | None:
    """围栏内自动放行, 但显式 deny / ask 规则先看. 命中规则返回 None 走正常流程."""
    for text in texts:
        matched = _matching(permission, text)
        if matched.deny is not None or matched.ask is not None:
            return None
        if _exact_rule(permission, text, PermissionBehavior.DENY) is not None:
            return None
        if _exact_rule(permission, text, PermissionBehavior.ASK) is not None:
            return None
    return allow(reason=sandbox_override_reason("sandboxed command auto-allowed"))


def _suggestions(
    results: tuple[tuple[str, PermissionResult, CommandUnit], ...],
) -> tuple[PermissionUpdate, ...]:
    """没被 allow 的子命令各给一条前缀 (或精确) 规则, 合成一条建议."""
    values: list[PermissionRuleValue] = []
    for text, result, unit in results:
        if result.is_allow:
            continue
        if unit.argv and not unit.argv[0].startswith("-"):
            content = f"{unit.name} {unit.argv[0]}:*"
        else:
            content = text
        value = PermissionRuleValue(tool_name=BASH_TOOL_NAME, rule_content=content)
        if value not in values:
            values.append(value)
    if not values:
        return ()
    return (
        PermissionUpdate(
            type=PermissionUpdateType.ADD_RULES,
            destination=PermissionUpdateDestination.PROJECT_SETTINGS,
            rules=tuple(values),
            behavior=PermissionBehavior.ALLOW,
        ),
    )


def bash_tool_has_permission(
    input: Mapping[str, object], context: ToolUseContext, *, shell_kind: ShellKind
) -> PermissionResult:
    command = str(input.get("command", "")).strip()
    state = context.get_app_state()
    permission = state.tool_permission_context
    plan = parse_for_permissions(command, shell_kind, context.cwd)

    # 0. too-complex.
    complex_reason = too_complex_reason(plan)
    if complex_reason:
        early = _check_early_exit_deny(command, permission)
        if early is not None:
            return early
        reason = other_reason(complex_reason)
        return ask(create_permission_request_message(BASH_TOOL_NAME, reason), reason)

    units = plan.units
    texts = tuple(command_text(unit, shell_kind) for unit in units)

    # 1. 沙箱内自动放行 (尊重 deny / ask 规则).
    if state.sandbox.auto_allow_bash_if_sandboxed and should_use_sandbox(
        input, state.sandbox
    ):
        auto = _check_sandbox_auto_allow(texts, permission)
        if auto is not None:
            return auto

    # 2. 整条命令精确匹配.
    exact = bash_tool_check_exact_match_permission(command, permission)
    if exact.is_deny or exact.is_ask:
        return exact

    contexts, directory_error = directory_contexts(plan, context)
    # 3. 逐子命令.
    results: list[tuple[str, PermissionResult, CommandUnit]] = []
    for index, (unit, text) in enumerate(zip(units, texts, strict=True)):
        unit_contexts = contexts[index] if contexts else (context,)
        checked_unit = unit
        if unit.name == "cd" and unit.argv[:1] == ("--",):
            checked_unit = replace(unit, argv=unit.argv[1:])
        decisions = tuple(
            bash_tool_check_permission(checked_unit, text, candidate, permission)
            for candidate in unit_contexts
        )
        decision = next((d for d in decisions if d.is_deny), None)
        decision = decision or next((d for d in decisions if d.is_ask), None)
        decision = decision or next(
            (d for d in decisions if not d.is_allow), decisions[0]
        )
        results.append((text, decision, unit))
    reason_pairs = tuple((text, result) for text, result, _ in results)
    for _, result, _ in results:
        if result.is_deny:
            return deny(result.message, subcommand_results_reason(reason_pairs))
    if directory_error:
        if exact.is_allow:
            return allow(input, exact.decision_reason)
        return ask(directory_error, other_reason(directory_error))
    if all(result.is_allow for _, result, _ in results):
        return allow(input, subcommand_results_reason(reason_pairs))
    if exact.is_allow:
        return allow(input, exact.decision_reason)
    suggestions = _suggestions(tuple(results))
    if len(results) == 1:
        # 只有一个子命令时用它自己的理由与措辞, 不套"多个操作"那句.
        single = results[0][1]
        return ask(
            single.message
            or create_permission_request_message(
                BASH_TOOL_NAME, single.decision_reason
            ),
            single.decision_reason,
            suggestions=suggestions,
        )
    reason = subcommand_results_reason(reason_pairs)
    return ask(
        create_permission_request_message(BASH_TOOL_NAME, reason),
        reason,
        suggestions=suggestions,
    )
