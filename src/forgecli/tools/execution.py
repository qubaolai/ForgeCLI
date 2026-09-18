"""执行链 (ADR-0054 决策 3; 对应 services/tools/toolExecution.ts 的 runToolUse).

七步, 顺序固定, 每一步失败都是一条 is_error 的 tool_result 回给模型:

    查找 -> schema 校验 -> validate_input -> 补派生字段 -> 权限 -> call -> 落盘与回流

Claude Code 在权限前后各有一个用户配置的 hook (PreToolUse / PostToolUse); 本仓库没有
(ADR-0054 决策 6 已撤), 需要时再加.

一次 tool_use 只产生一条 tool_result; 中间事实 (校验过了, 裁决了, 问了人, 开始了,
结束了) 经 `context.observer` 发出去.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import jsonschema

from forgecli.common.cancellation import CancelledError
from forgecli.common.observability.log import get_log
from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionResult
from forgecli.core.tool.tool import (
    CanUseToolFn,
    ToolProgress,
    ToolUseContext,
)
from forgecli.core.tool.tool_call import ToolCall
from forgecli.tools.pool import find_tool_by_name
from forgecli.tools.result_storage import persist_if_needed

__all__ = ["ToolUseOutcome", "run_tool_use"]

_log = get_log(__name__)

_TOOL_USE_ERROR_OPEN = "<tool_use_error>"
_TOOL_USE_ERROR_CLOSE = "</tool_use_error>"

type _JsonValue = (
    str | int | float | bool | None | Mapping[str, _JsonValue] | Sequence[_JsonValue]
)


@dataclass(frozen=True)
class ToolUseOutcome:
    """一次 tool_use 的全部产出: 回给模型的块, 顺带的消息, 以及给循环看的几项事实."""

    block: ToolResultBlock
    new_messages: tuple[ChatMessage, ...] = ()
    # ok / error / denied / rejected / cancelled
    status: str = "ok"
    executed: bool = False
    # 人明确拒绝了 (与策略拒绝分开: 人拒绝的是意图, 不许换说法重试).
    user_rejected: bool = False
    # 工具交出了需要人裁决的东西, 本轮到此为止 (ADR-0023).
    await_user_decision: bool = False


def _error_block(tool_use_id: str, text: str) -> ToolResultBlock:
    return ToolResultBlock(tool_call_id=tool_use_id, content=text, is_error=True)


def _wrapped_error(text: str) -> str:
    return f"{_TOOL_USE_ERROR_OPEN}{text}{_TOOL_USE_ERROR_CLOSE}"


def _format_schema_error(tool_name: str, error: jsonschema.ValidationError) -> str:
    where = "/".join(str(part) for part in error.absolute_path)
    if not where:
        where = "input"
    return f"InputValidationError: {tool_name} {where}: {error.message}"


def _arguments_for_event(input: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple((key, str(value)) for key, value in input.items())


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def run_tool_use(
    tool_use: ToolCall,
    context: ToolUseContext,
    can_use_tool: CanUseToolFn,
    parent_message: ChatMessage | None,
) -> ToolUseOutcome:
    tool_use_id = tool_use.tool_call_id
    started = time.perf_counter()

    # 1. 查找.
    tool = find_tool_by_name(context.options.tools, tool_use.name)
    if tool is None:
        return _not_run(
            tool_use_id, f"No such tool available: {tool_use.name}", "error"
        )
    if context.abort.cancelled:
        return _not_run(tool_use_id, "Tool execution was cancelled", "cancelled")

    # 2. schema.
    raw_input = dict(tool_use.arguments)
    validator = jsonschema.Draft202012Validator(dict(tool.input_schema))
    # 模型协议传回 JSON, ToolCall 的通用 Mapping[str, object] 未表达这个约束.
    schema_errors = list(validator.iter_errors(cast(_JsonValue, raw_input)))
    if schema_errors:
        message = _format_schema_error(tool.name, schema_errors[0])
        _log.info("tool.input_invalid", tool=tool.name, message=message[:200])
        return _not_run(tool_use_id, _wrapped_error(message), "error")

    # 3. validate_input.
    try:
        validation = tool.validate_input(raw_input, context)
    except Exception as exc:  # noqa: BLE001 - 校验代码出错按校验失败回给模型
        _log.exception("tool.validate_failed", tool=tool.name)
        validation_message = f"{type(exc).__name__}: {exc}"
        return _not_run(tool_use_id, _wrapped_error(validation_message), "error")
    if not validation.ok:
        _log.info(
            "tool.validation_error", tool=tool.name, message=validation.message[:200]
        )
        return _not_run(tool_use_id, _wrapped_error(validation.message), "error")

    # 4. 派生字段在副本上补, 给权限看; 原件给 call.
    observable_input: dict[str, object] = dict(raw_input)
    tool.backfill_observable_input(observable_input)
    context.observer.tool_validated(
        tool.name,
        tool_use_id=tool_use_id,
        activity=tool.get_activity_description(observable_input) or tool.name,
        arguments=_arguments_for_event(observable_input),
        elapsed_ms=_elapsed_ms(started),
    )

    # 5. 权限.
    decision = can_use_tool(
        tool, observable_input, context, parent_message, tool_use_id
    )
    if decision.is_deny:
        rejected = _is_user_rejection(decision)
        status = "rejected" if rejected else "denied"
        return ToolUseOutcome(
            block=_error_block(tool_use_id, decision.message),
            status=status,
            executed=False,
            user_rejected=rejected,
        )
    call_input: Mapping[str, object] = raw_input
    if (
        decision.updated_input is not None
        and decision.updated_input != observable_input
    ):
        call_input = decision.updated_input

    # 6. call.
    context.observer.tool_started(tool.name, tool_use_id=tool_use_id)
    call_started = time.perf_counter()

    def on_progress(progress: ToolProgress) -> None:
        _log.debug("tool.progress", tool=tool.name, text=progress.text[-200:])

    try:
        result = tool.call(
            call_input, context, can_use_tool, parent_message, on_progress
        )
    except CancelledError:
        context.observer.tool_cancelled(
            tool.name, tool_use_id=tool_use_id, elapsed_ms=_elapsed_ms(call_started)
        )
        return ToolUseOutcome(
            block=_error_block(tool_use_id, "Tool execution was cancelled"),
            status="cancelled",
            executed=True,
        )
    except Exception as exc:  # noqa: BLE001 - 工具抛什么都归成一条给模型的错误
        _log.exception("tool.call_failed", tool=tool.name)
        message = _classify_error(exc)
        context.observer.tool_completed(
            tool.name,
            tool_use_id=tool_use_id,
            status="error",
            elapsed_ms=_elapsed_ms(call_started),
            result_summary="",
            error_summary=message,
            bytes_out=0,
            persisted=False,
            executed=True,
        )
        return ToolUseOutcome(
            block=_error_block(tool_use_id, message), status="error", executed=True
        )

    # 7. 映射与落盘.
    block = tool.map_tool_result_to_block(result.data, tool_use_id)
    text, persisted = persist_if_needed(
        block.content,
        tool,
        tool_use_id=tool_use_id,
        tool_results_dir=context.tool_results_dir,
    )
    if decision.accept_feedback:
        text = text + "\n\n" + decision.accept_feedback
    final = ToolResultBlock(
        tool_call_id=tool_use_id, content=text, is_error=block.is_error
    )
    summary = tool.get_tool_use_summary(call_input) or ""
    context.observer.tool_completed(
        tool.name,
        tool_use_id=tool_use_id,
        status="error" if block.is_error else "ok",
        elapsed_ms=_elapsed_ms(call_started),
        result_summary=summary,
        error_summary=block.content[:200] if block.is_error else "",
        bytes_out=len(block.content.encode("utf-8")),
        persisted=persisted,
        executed=True,
    )
    if _log.enabled_for_debug():
        _log.debug("tool.output", tool=tool.name, text=text)
    return ToolUseOutcome(
        block=final,
        new_messages=result.new_messages,
        status="error" if block.is_error else "ok",
        executed=True,
        await_user_decision=result.await_user_decision,
    )


def _not_run(tool_use_id: str, text: str, status: str) -> ToolUseOutcome:
    return ToolUseOutcome(
        block=_error_block(tool_use_id, text), status=status, executed=False
    )


def _is_user_rejection(decision: PermissionResult) -> bool:
    reason = decision.decision_reason
    return reason is not None and reason.reason in (
        "user rejected",
        "permission prompt unanswered",
    )


def _classify_error(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        text = type(exc).__name__
    return f"Error: {text}"
