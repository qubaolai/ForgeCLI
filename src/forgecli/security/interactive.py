"""ask 的去向: 摆卡片, 等人点, 把回答翻回决定 (ADR-0055 决策 4 / 6).

对应 Claude Code 的 useCanUseTool: 先 `has_permissions_to_use_tool`, 只有 ask 才经
`HumanPromptService` 摆一张审批卡片 (ADR-0043 的通道, 与 AskUserQuestion 共用).
卡片上的按钮来自决定里的 `suggestions`; 用户选了哪一项, 就施加对应的 PermissionUpdate.

auto 模式的分类器 (ADR-0058) 在 `has_permissions_to_use_tool` 里面: 到这里还是 ask 的,
要么模式不是 auto, 要么分类器拦得太多退回来问人.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace

from forgecli.common.observability.log import get_log
from forgecli.core.message import ChatMessage
from forgecli.core.permissions import (
    PermissionBehavior,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
    PermissionUpdate,
    PermissionUpdateDestination,
    PermissionUpdateType,
    allow,
    deny,
    other_reason,
    rule_text,
)
from forgecli.core.tool.tool import CanUseToolFn, Tool, ToolUseContext
from forgecli.human_interaction.prompt import HumanPrompt, PromptChoice, PromptKind
from forgecli.security.auto_mode_classifier import AutoModeClassifier
from forgecli.security.permission_update import PermissionUpdateApplier
from forgecli.security.permissions import has_permissions_to_use_tool

__all__ = [
    "REJECT_MESSAGE",
    "make_can_use_tool",
    "suggestion_label",
    "update_rule_texts",
]

_log = get_log(__name__)

# 用户拒绝时回给模型的话, 与 Claude Code 的 REJECT_MESSAGE 逐字同: 它是给模型的指令.
REJECT_MESSAGE = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected "
    "(eg. if it was a file edit, the new_string was NOT written to the file). STOP "
    "what you are doing and wait for the user to tell you how to proceed."
)
_UNANSWERED_MESSAGE = (
    "The permission prompt was not answered: no one is available to approve this "
    "tool use in the current context. Do not retry the same tool use."
)

_CHOICE_YES = "yes"
_CHOICE_NO = "no"
_CHOICE_PREFIX = "suggestion_"


def update_rule_texts(update: PermissionUpdate) -> tuple[str, ...]:
    behavior = update.behavior
    if behavior is None:
        behavior = PermissionBehavior.ALLOW
    texts: list[str] = []
    for value in update.rules:
        rule = PermissionRule(
            source=PermissionRuleSource.SESSION,
            rule_behavior=behavior,
            rule_value=value,
        )
        texts.append(rule_text(rule))
    return tuple(texts)


def suggestion_label(update: PermissionUpdate) -> str:
    """按钮上那一行. 界面 (终端与 Web) 共用, 不各自写一份."""
    if update.type is PermissionUpdateType.SET_MODE and update.mode is not None:
        return f"切到 {update.mode.value}, 本会话内不再问这类操作"
    if update.type is PermissionUpdateType.ADD_DIRECTORIES:
        return f"把 {', '.join(update.directories)} 加入工作目录 (本会话)"
    joined = ", ".join(update_rule_texts(update))
    if update.destination is PermissionUpdateDestination.SESSION:
        return f"本会话始终允许 {joined}"
    if update.destination is PermissionUpdateDestination.USER_SETTINGS:
        return f"所有项目始终允许 {joined}"
    return f"本项目始终允许 {joined}"


def _new_prompt_id() -> str:
    return f"perm_{uuid.uuid4().hex[:12]}"


def _jsonable(value: object) -> object:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return str(value)


def _detail(
    tool: Tool,
    input: Mapping[str, object],
    result: PermissionResult,
    tool_use_id: str,
) -> dict[str, object]:
    suggestions: list[dict[str, object]] = []
    for index, update in enumerate(result.suggestions):
        mode = ""
        if update.mode is not None:
            mode = update.mode.value
        suggestions.append(
            {
                "value": f"{_CHOICE_PREFIX}{index}",
                "type": update.type.value,
                "destination": update.destination.value,
                "label": suggestion_label(update),
                "rules": list(update_rule_texts(update)),
                "directories": list(update.directories),
                "mode": mode,
            }
        )
    reason = ""
    if result.decision_reason is not None:
        reason = result.decision_reason.describe()
    return {
        "tool_name": tool.name,
        "tool_use_id": tool_use_id,
        "user_facing_name": tool.user_facing_name(input),
        "activity": tool.get_activity_description(input) or "",
        "summary": tool.get_tool_use_summary(input) or "",
        "input": {key: _jsonable(value) for key, value in input.items()},
        "message": result.message,
        "reason": reason,
        "blocked_path": result.blocked_path,
        "destructive": tool.is_destructive(input),
        "suggestions": suggestions,
    }


def make_can_use_tool(
    applier: PermissionUpdateApplier,
    *,
    classifier: AutoModeClassifier | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> CanUseToolFn:
    def can_use_tool(
        tool: Tool,
        input: Mapping[str, object],
        context: ToolUseContext,
        parent_message: ChatMessage | None,
        tool_use_id: str,
    ) -> PermissionResult:
        started = clock()
        result = has_permissions_to_use_tool(
            tool, input, context, classifier=classifier
        )
        elapsed = (clock() - started) * 1000.0
        reason = ""
        if result.decision_reason is not None:
            reason = result.decision_reason.describe()
        context.observer.permission_decided(
            tool.name,
            tool_use_id=tool_use_id,
            behavior=result.behavior.value,
            reason=reason,
            message=result.message,
            elapsed_ms=elapsed,
        )
        if not result.is_ask:
            return result
        return _prompt(tool, input, context, result, tool_use_id, applier)

    return can_use_tool


def _prompt(
    tool: Tool,
    input: Mapping[str, object],
    context: ToolUseContext,
    result: PermissionResult,
    tool_use_id: str,
    applier: PermissionUpdateApplier,
) -> PermissionResult:
    prompt_id = _new_prompt_id()
    choices: list[PromptChoice] = [PromptChoice(_CHOICE_YES, "允许这一次")]
    for index, update in enumerate(result.suggestions):
        choices.append(
            PromptChoice(f"{_CHOICE_PREFIX}{index}", suggestion_label(update))
        )
    choices.append(PromptChoice(_CHOICE_NO, "拒绝"))
    prompt = HumanPrompt(
        prompt_id=prompt_id,
        kind=PromptKind.APPROVAL,
        title=tool.user_facing_name(input),
        body=result.message,
        choices=tuple(choices),
        free_text=False,
        detail=_detail(tool, input, result, tool_use_id),
    )
    context.observer.permission_prompted(
        tool.name,
        tool_use_id=tool_use_id,
        prompt_id=prompt_id,
        message=result.message,
        suggestions=tuple(choice.label for choice in choices),
    )
    answer = context.prompts.ask(prompt)
    if not answer.resolved:
        _answered(context, tool, tool_use_id, prompt_id, "unanswered", "", answer.note)
        return deny(_UNANSWERED_MESSAGE, other_reason("permission prompt unanswered"))
    if answer.choice == _CHOICE_NO:
        _answered(context, tool, tool_use_id, prompt_id, "denied", answer.choice, "")
        return deny(REJECT_MESSAGE, other_reason("user rejected"))
    if answer.choice.startswith(_CHOICE_PREFIX):
        index = int(answer.choice[len(_CHOICE_PREFIX) :])
        if 0 <= index < len(result.suggestions):
            _apply(applier, result.suggestions[index], context)
    _answered(context, tool, tool_use_id, prompt_id, "approved", answer.choice, "")
    updated_input = input
    if result.updated_input is not None:
        updated_input = result.updated_input
    return allow(updated_input, other_reason("user approved"))


def _answered(
    context: ToolUseContext,
    tool: Tool,
    tool_use_id: str,
    prompt_id: str,
    outcome: str,
    choice: str,
    note: str,
) -> None:
    context.observer.permission_answered(
        tool.name,
        tool_use_id=tool_use_id,
        prompt_id=prompt_id,
        outcome=outcome,
        choice=choice,
        note=note,
    )


def _apply(
    applier: PermissionUpdateApplier, update: PermissionUpdate, context: ToolUseContext
) -> None:
    state = context.get_app_state()
    try:
        permission = applier.apply(update, state.tool_permission_context)
    except OSError:
        _log.exception("permissions.update_failed", update=update.type.value)
        return
    context.set_app_state(replace(state, tool_permission_context=permission))
