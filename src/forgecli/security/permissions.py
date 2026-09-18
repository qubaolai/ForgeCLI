"""裁决顺序 (ADR-0055 决策 4; 对应 utils/permissions/permissions.ts).

`has_permissions_to_use_tool` 是唯一入口. 顺序是安全优先级, 一步都不能调换:

    整工具 deny -> 整工具 ask -> 工具自己的 check_permissions -> 工具 deny
    -> 需要人在场的 ask / 内容级 ask 规则 / 安全检查 (bypass 也问)
    -> bypass 放行 -> 整工具 allow -> 其余 ask

外层再做三件事: dont_ask 把 ask 变成 deny; auto 把 ask 交给安全分类器 (ADR-0058
决策 4, 在"没有人机界面"之前查, 让无人值守的入口也能用 auto); 没有人机界面时 ask 也
变成 deny.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from forgecli.common.observability.log import get_log
from forgecli.core.permissions import (
    DecisionReasonType,
    DenialTrackingState,
    PermissionBehavior,
    PermissionDecisionReason,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
    ResultBehavior,
    ToolPermissionContext,
    allow,
    ask,
    classifier_reason,
    deny,
    mode_reason,
    other_reason,
    rule_reason,
    rule_text,
)
from forgecli.core.tool.names import (
    ASK_USER_QUESTION_TOOL_NAME,
    BASH_TOOL_NAME,
    FILE_READ_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    LSP_TOOL_NAME,
    TODO_WRITE_TOOL_NAME,
)
from forgecli.core.tool.tool import Tool, ToolUseContext
from forgecli.security.auto_mode import is_auto_mode_active
from forgecli.security.auto_mode_classifier import AutoModeClassifier
from forgecli.security.denial_tracking import (
    MAX_TOTAL_DENIALS,
    record_denial,
    record_success,
    should_fallback_to_prompting,
)
from forgecli.security.permission_rule_parser import permission_rule_value_from_string
from forgecli.security.sandbox_rules import should_use_sandbox

__all__ = [
    "AUTO_MODE_ALLOWLISTED_TOOLS",
    "DENIAL_WORKAROUND_GUIDANCE",
    "DONT_ASK_REJECT_MESSAGE",
    "build_auto_mode_rejection_message",
    "build_classifier_unavailable_message",
    "create_permission_request_message",
    "get_allow_rules",
    "get_ask_rule_for_tool",
    "get_ask_rules",
    "get_deny_rule_for_tool",
    "get_deny_rules",
    "get_rule_by_contents_for_tool",
    "has_permissions_to_use_tool",
    "tool_always_allowed_rule",
    "tool_matches_rule",
]

_log = get_log(__name__)

# 来源的求值序: 与 Claude Code 的 PERMISSION_RULE_SOURCES 同, policy 最先.
_SOURCES: tuple[PermissionRuleSource, ...] = (
    PermissionRuleSource.POLICY_SETTINGS,
    PermissionRuleSource.USER_SETTINGS,
    PermissionRuleSource.PROJECT_SETTINGS,
    PermissionRuleSource.CLI_ARG,
    PermissionRuleSource.SESSION,
)


def DONT_ASK_REJECT_MESSAGE(tool_name: str) -> str:  # noqa: N802
    return (
        f"Permission to use {tool_name} was denied because the session is running "
        "in dont_ask mode and this tool use requires approval."
    )


# 被拒之后模型该怎么办, 与 Claude Code 的 DENIAL_WORKAROUND_GUIDANCE 逐字同: 允许换一种
# 自然的做法, 不许绕.
DENIAL_WORKAROUND_GUIDANCE = (
    "IMPORTANT: You *may* attempt to accomplish this action using other tools that "
    "might naturally be used to accomplish this goal, e.g. using head instead of "
    "cat. But you *should not* attempt to work around this denial in malicious "
    "ways, e.g. do not use your ability to run tests to execute non-test actions. "
    "You should only try to work around this restriction in reasonable ways that "
    "do not attempt to bypass the intent behind this denial. If you believe this "
    "capability is essential to complete the user's request, STOP and explain to "
    "the user what you were trying to do and why you need this permission. Let "
    "the user decide how to proceed."
)

_AUTO_MODE_REJECTION_PREFIX = "Permission for this action has been denied. Reason: "


def build_auto_mode_rejection_message(reason: str) -> str:
    """分类器拦下时回给模型的话 (对应 buildYoloRejectionMessage)."""
    return (
        f"{_AUTO_MODE_REJECTION_PREFIX}{reason}. If you have other tasks that don't "
        "depend on this action, continue working on those. "
        f"{DENIAL_WORKAROUND_GUIDANCE} To allow this type of action in the future, "
        "the user can add a permission rule like Bash(<prefix>:*) to the "
        "permissions.allow list, or a sentence describing the allowed action to "
        "autoMode.allow, in their Forge settings. At the end of your session, "
        "recommend what rules to add so you don't get blocked again."
    )


def build_classifier_unavailable_message(tool_name: str, model: str) -> str:
    """分类器没问到 (网络错, 回答不合 schema) 时回给模型的话: 说清是暂时的."""
    who = model or "The auto mode classifier"
    return (
        f"{who} is temporarily unavailable, so auto mode cannot determine the "
        f"safety of {tool_name} right now. Wait briefly and then try this action "
        "again. If it keeps failing, continue with other tasks that don't require "
        "this action and come back to it later. Note: reading files, searching "
        "code, and other read-only operations do not require the classifier and "
        "can still be used."
    )


# 不必过分类器的工具 (对应 classifierDecision.ts 的 SAFE_YOLO_ALLOWLISTED_TOOLS): 只读,
# 搜索, 待办与提问. 写工具不在这里: 它们走 accept_edits 那条快路 (工作目录内放行,
# 目录外才问分类器).
AUTO_MODE_ALLOWLISTED_TOOLS: frozenset[str] = frozenset(
    {
        FILE_READ_TOOL_NAME,
        GREP_TOOL_NAME,
        GLOB_TOOL_NAME,
        LSP_TOOL_NAME,
        TODO_WRITE_TOOL_NAME,
        ASK_USER_QUESTION_TOOL_NAME,
    }
)


def _rules_of(
    table: Mapping[PermissionRuleSource, tuple[str, ...]], behavior: PermissionBehavior
) -> tuple[PermissionRule, ...]:
    rules: list[PermissionRule] = []
    for source in _SOURCES:
        for rule_string in table.get(source, ()):
            rules.append(
                PermissionRule(
                    source=source,
                    rule_behavior=behavior,
                    rule_value=permission_rule_value_from_string(rule_string),
                )
            )
    return tuple(rules)


def get_allow_rules(context: ToolPermissionContext) -> tuple[PermissionRule, ...]:
    return _rules_of(context.always_allow_rules, PermissionBehavior.ALLOW)


def get_deny_rules(context: ToolPermissionContext) -> tuple[PermissionRule, ...]:
    return _rules_of(context.always_deny_rules, PermissionBehavior.DENY)


def get_ask_rules(context: ToolPermissionContext) -> tuple[PermissionRule, ...]:
    return _rules_of(context.always_ask_rules, PermissionBehavior.ASK)


def tool_matches_rule(tool: Tool, rule: PermissionRule) -> bool:
    """整工具规则 (没有括号内容) 是否命中这个工具. 认别名."""
    if rule.rule_value.rule_content is not None:
        return False
    name = rule.rule_value.tool_name
    return name == tool.name or name in tool.aliases


def tool_always_allowed_rule(
    context: ToolPermissionContext, tool: Tool
) -> PermissionRule | None:
    for rule in get_allow_rules(context):
        if tool_matches_rule(tool, rule):
            return rule
    return None


def get_deny_rule_for_tool(
    context: ToolPermissionContext, tool: Tool
) -> PermissionRule | None:
    for rule in get_deny_rules(context):
        if tool_matches_rule(tool, rule):
            return rule
    return None


def get_ask_rule_for_tool(
    context: ToolPermissionContext, tool: Tool
) -> PermissionRule | None:
    for rule in get_ask_rules(context):
        if tool_matches_rule(tool, rule):
            return rule
    return None


def get_rule_by_contents_for_tool(
    context: ToolPermissionContext,
    tool_name: str,
    rule_content: str,
    behavior: PermissionBehavior,
) -> PermissionRule | None:
    """内容逐字相等的那条规则 (精确匹配)."""
    if behavior is PermissionBehavior.ALLOW:
        rules = get_allow_rules(context)
    elif behavior is PermissionBehavior.DENY:
        rules = get_deny_rules(context)
    else:
        rules = get_ask_rules(context)
    for rule in rules:
        if (
            rule.rule_value.tool_name == tool_name
            and rule.rule_value.rule_content == rule_content
        ):
            return rule
    return None


def _source_display(source: PermissionRuleSource) -> str:
    return source.value.replace("_", " ")


def create_permission_request_message(
    tool_name: str, reason: PermissionDecisionReason | None = None
) -> str:
    if reason is not None:
        if reason.type is DecisionReasonType.RULE and reason.rule is not None:
            return (
                f"Permission rule '{rule_text(reason.rule)}' from "
                f"{_source_display(reason.rule.source)} requires approval for this "
                f"{tool_name} command"
            )
        if reason.type is DecisionReasonType.SUBCOMMAND_RESULTS:
            needs: list[str] = []
            for command, result in reason.subcommand_results:
                if result.behavior in (ResultBehavior.ASK, ResultBehavior.PASSTHROUGH):
                    needs.append(command)
            if needs:
                noun = "part" if len(needs) == 1 else "parts"
                verb = "requires" if len(needs) == 1 else "require"
                return (
                    f"This {tool_name} command contains multiple operations. The "
                    f"following {noun} {verb} approval: {', '.join(needs)}"
                )
            return (
                f"This {tool_name} command contains multiple operations that "
                "require approval"
            )
        if reason.type is DecisionReasonType.SANDBOX_OVERRIDE:
            return "Run outside of the sandbox"
        if reason.type is DecisionReasonType.MODE and reason.mode is not None:
            return (
                f"Current permission mode ({reason.mode.value}) requires approval "
                f"for this {tool_name} command"
            )
        if reason.reason:
            return reason.reason
    return (
        f"Forge requested permissions to use {tool_name}, but you haven't granted "
        "it yet."
    )


def _sandbox_can_auto_allow(
    tool: Tool, input: Mapping[str, object], context: ToolUseContext
) -> bool:
    """整工具 ask 规则的例外: 会进围栏的 Bash 命令交给工具自己的裁决 (那里仍先查
    deny / ask)."""
    if tool.name != BASH_TOOL_NAME:
        return False
    sandbox = context.get_app_state().sandbox
    if not sandbox.auto_allow_bash_if_sandboxed:
        return False
    return should_use_sandbox(input, sandbox)


def _tool_permission_result(
    tool: Tool, input: Mapping[str, object], context: ToolUseContext
) -> PermissionResult:
    """1c: 问工具自己. 抛异常按 passthrough 算 (fail closed 到 ask, 不到 allow)."""
    try:
        return tool.check_permissions(input, context)
    except Exception:  # noqa: BLE001 - 工具的裁决代码出错不该放行, 也不该炸掉执行链
        _log.exception("permissions.tool_check_failed", tool=tool.name)
        return PermissionResult(
            behavior=ResultBehavior.PASSTHROUGH,
            message=create_permission_request_message(tool.name),
        )


def _is_content_ask_rule(result: PermissionResult) -> bool:
    reason = result.decision_reason
    return (
        result.is_ask
        and reason is not None
        and reason.type is DecisionReasonType.RULE
        and reason.rule is not None
        and reason.rule.rule_behavior is PermissionBehavior.ASK
    )


def _is_safety_check(result: PermissionResult) -> bool:
    reason = result.decision_reason
    return (
        result.is_ask
        and reason is not None
        and reason.type is DecisionReasonType.SAFETY_CHECK
    )


def _inner(
    tool: Tool, input: Mapping[str, object], context: ToolUseContext
) -> PermissionResult:
    permission = context.get_app_state().tool_permission_context

    # 1a. 整工具 deny.
    deny_rule = get_deny_rule_for_tool(permission, tool)
    if deny_rule is not None:
        return deny(
            f"Permission to use {tool.name} has been denied.", rule_reason(deny_rule)
        )

    # 1b. 整工具 ask; 会进围栏的 Bash 交给 1c.
    ask_rule = get_ask_rule_for_tool(permission, tool)
    if ask_rule is not None and not _sandbox_can_auto_allow(tool, input, context):
        return ask(create_permission_request_message(tool.name), rule_reason(ask_rule))

    # 1c. 工具自己.
    result = _tool_permission_result(tool, input, context)

    # 1d. 工具说 deny.
    if result.is_deny:
        return result

    # 1e. 需要人在场的工具说 ask, bypass 也问.
    if tool.requires_user_interaction() and result.is_ask:
        return result

    # 1f. 内容级 ask 规则, bypass 也问.
    if _is_content_ask_rule(result):
        return result

    # 1g. 安全检查, bypass 也问.
    if _is_safety_check(result):
        return result

    # 2a. 模式放行.
    permission = context.get_app_state().tool_permission_context
    bypass = permission.mode is PermissionMode.BYPASS_PERMISSIONS or (
        permission.mode is PermissionMode.PLAN
        and permission.is_bypass_permissions_mode_available
    )
    if bypass:
        return allow(_updated_input_or(result, input), mode_reason(permission.mode))

    # 2b. 整工具 allow.
    allowed_rule = tool_always_allowed_rule(permission, tool)
    if allowed_rule is not None:
        return allow(_updated_input_or(result, input), rule_reason(allowed_rule))

    # 3. passthrough -> ask.
    if result.is_passthrough:
        return ask(
            create_permission_request_message(tool.name, result.decision_reason),
            result.decision_reason,
            suggestions=result.suggestions,
            blocked_path=result.blocked_path,
        )
    return result


def _updated_input_or(
    result: PermissionResult, input: Mapping[str, object]
) -> Mapping[str, object]:
    if result.updated_input is not None:
        return result.updated_input
    return input


def has_permissions_to_use_tool(
    tool: Tool,
    input: Mapping[str, object],
    context: ToolUseContext,
    *,
    classifier: AutoModeClassifier | None = None,
) -> PermissionResult:
    """裁决入口. 返回的 behavior 只会是 allow / deny / ask.

    `classifier` 是 auto 模式的去处; 没接 (None) 时 auto 与 default 一样问人."""
    result = _inner(tool, input, context)
    permission = context.get_app_state().tool_permission_context
    if result.is_allow:
        # auto 下任何一次放行 (包括规则放的) 都打断"连续被拦"的计数.
        if is_auto_mode_active(permission):
            _persist_denials(context, record_success(_denials(context)))
        return result
    if result.is_ask:
        if permission.mode is PermissionMode.DONT_ASK:
            return deny(
                DONT_ASK_REJECT_MESSAGE(tool.name),
                mode_reason(PermissionMode.DONT_ASK),
            )
        if is_auto_mode_active(permission) and classifier is not None:
            return _auto_mode_decision(tool, input, context, result, classifier)
        if permission.should_avoid_permission_prompts:
            return _no_prompt_available(result)
    return result


def _no_prompt_available(result: PermissionResult) -> PermissionResult:
    return deny(
        result.message,
        other_reason(
            "This tool use requires interactive approval and permission "
            "prompts are not available in this context"
        ),
    )


# ---- auto 模式 (ADR-0058 决策 4; 对应 hasPermissionsToUseTool 里 mode === 'auto'
# 那一段) ----


def _denials(context: ToolUseContext) -> DenialTrackingState:
    return context.get_app_state().denial_tracking


def _persist_denials(context: ToolUseContext, state: DenialTrackingState) -> None:
    current = context.get_app_state()
    if current.denial_tracking == state:
        return
    context.set_app_state(replace(current, denial_tracking=state))


def _with_mode(context: ToolUseContext, mode: PermissionMode) -> ToolUseContext:
    """同一份上下文, 只是权限上下文的模式换成 mode: 给"换成 accept_edits 会不会放行"
    这一问用."""
    state = context.get_app_state()
    permission = replace(state.tool_permission_context, mode=mode)
    swapped = replace(state, tool_permission_context=permission)
    return replace(context, get_app_state=lambda: swapped)


def _auto_mode_decision(
    tool: Tool,
    input: Mapping[str, object],
    context: ToolUseContext,
    result: PermissionResult,
    classifier: AutoModeClassifier,
) -> PermissionResult:
    permission = context.get_app_state().tool_permission_context
    reason = result.decision_reason

    # 安全检查里分类器管不了的那几种 (可疑的 Windows 路径, UNC) 仍然要人: 分类器,
    # 快路与白名单一个都不放.
    if (
        reason is not None
        and reason.type is DecisionReasonType.SAFETY_CHECK
        and not reason.classifier_approvable
    ):
        if permission.should_avoid_permission_prompts:
            return _no_prompt_available(result)
        return result
    # 要人在场的工具 (计划评审一类) 说 ask, 分类器替不了.
    if tool.requires_user_interaction():
        return result

    # 快路一: 换成 accept_edits 就会放行的 (工作目录内的编辑与文件系统命令) 不必问
    # 分类器, 省一次模型调用.
    accept_edits = _tool_permission_result(
        tool, input, _with_mode(context, PermissionMode.ACCEPT_EDITS)
    )
    if accept_edits.is_allow:
        _persist_denials(context, record_success(_denials(context)))
        _log.debug("permissions.auto_fast_path", tool=tool.name, path="accept_edits")
        return allow(
            _updated_input_or(accept_edits, input), mode_reason(PermissionMode.AUTO)
        )

    # 快路二: 只读与提问类的工具.
    if tool.name in AUTO_MODE_ALLOWLISTED_TOOLS:
        _persist_denials(context, record_success(_denials(context)))
        _log.debug("permissions.auto_fast_path", tool=tool.name, path="allowlist")
        return allow(input, mode_reason(PermissionMode.AUTO))

    verdict = classifier.classify(
        messages=context.messages,
        tool=tool,
        input=input,
        tools=context.options.tools,
        permission=permission,
        session_id=context.session_id,
        turn_id=context.turn_id,
        cancel=context.abort,
    )
    if not verdict.should_block:
        _persist_denials(context, record_success(_denials(context)))
        return allow(input, classifier_reason(classifier.name, verdict.reason))

    if verdict.unavailable:
        # 没问到就当拦 (fail closed), 但话要说清是暂时的; 不计入拦截次数.
        return deny(
            build_classifier_unavailable_message(tool.name, verdict.model),
            classifier_reason(classifier.name, verdict.reason),
        )

    denials = record_denial(_denials(context))
    _persist_denials(context, denials)
    _log.warning(
        "permissions.auto_blocked",
        tool=tool.name,
        reason=verdict.reason,
        consecutive=denials.consecutive_denials,
        total=denials.total_denials,
    )
    if should_fallback_to_prompting(denials):
        return _fallback_to_prompting(
            classifier.name, context, result, denials, verdict.reason
        )
    return deny(
        build_auto_mode_rejection_message(verdict.reason),
        classifier_reason(classifier.name, verdict.reason),
    )


def _fallback_to_prompting(
    classifier_name: str,
    context: ToolUseContext,
    result: PermissionResult,
    denials: DenialTrackingState,
    latest_reason: str,
) -> PermissionResult:
    """拦得太多, 退回问人 (对应 handleDenialLimitExceeded): 卡片上带着分类器的理由.
    累计上限到了就清零, 让下一轮重新数; 连续上限不清, 由下一次放行打断."""
    hit_total = denials.total_denials >= MAX_TOTAL_DENIALS
    if hit_total:
        warning = (
            f"{denials.total_denials} actions were blocked this session. Please "
            "review the transcript before continuing."
        )
        _persist_denials(context, DenialTrackingState())
    else:
        warning = (
            f"{denials.consecutive_denials} consecutive actions were blocked. Please "
            "review the transcript before continuing."
        )
    reason = classifier_reason(
        classifier_name, f"{warning} Latest blocked action: {latest_reason}"
    )
    if context.get_app_state().tool_permission_context.should_avoid_permission_prompts:
        # 无人值守没人可问: 照拒, 话里带上累计次数, 让日志能看出它卡在这里.
        return deny(
            build_auto_mode_rejection_message(f"{warning} {latest_reason}"), reason
        )
    return replace(result, decision_reason=reason)
