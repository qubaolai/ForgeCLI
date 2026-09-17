"""调度 (ADR-0054 决策 4; 对应 services/tools/toolOrchestration.ts).

一轮里的多个 tool_use 按 `is_concurrency_safe(input)` 分批: 连续为真的合成一批并发跑
(线程池, 上限 FORGE_MAX_TOOL_USE_CONCURRENCY, 缺省 10), 其余各自一批串行. 结果按
请求序回给循环, 靠 tool_use_id 对应.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import jsonschema

from forgecli.common.observability.log import get_log
from forgecli.core.message import ChatMessage
from forgecli.core.tool.tool import CanUseToolFn, Tool, ToolUseContext
from forgecli.core.tool.tool_call import ToolCall
from forgecli.tools.execution import ToolUseOutcome, run_tool_use
from forgecli.tools.hooks import HookRunner
from forgecli.tools.pool import find_tool_by_name

__all__ = ["Batch", "max_tool_use_concurrency", "partition_tool_calls", "run_tools"]

_log = get_log(__name__)
_CONCURRENCY_ENV = "FORGE_MAX_TOOL_USE_CONCURRENCY"


def max_tool_use_concurrency() -> int:
    raw = os.environ.get(_CONCURRENCY_ENV, "")
    try:
        value = int(raw)
    except ValueError:
        return 10
    if value <= 0:
        return 10
    return value


@dataclass(frozen=True)
class Batch:
    is_concurrency_safe: bool
    blocks: tuple[ToolCall, ...]


def _is_concurrency_safe(tool: Tool | None, call: ToolCall) -> bool:
    if tool is None:
        return False
    try:
        jsonschema.Draft202012Validator(dict(tool.input_schema)).validate(
            dict(call.arguments)
        )
    except jsonschema.ValidationError:
        return False
    try:
        return bool(tool.is_concurrency_safe(call.arguments))
    except Exception:  # noqa: BLE001 - 判不出来按不安全算
        return False


def partition_tool_calls(
    calls: Sequence[ToolCall], tools: tuple[Tool, ...]
) -> tuple[Batch, ...]:
    batches: list[Batch] = []
    for call in calls:
        safe = _is_concurrency_safe(find_tool_by_name(tools, call.name), call)
        if safe and batches and batches[-1].is_concurrency_safe:
            last = batches[-1]
            batches[-1] = Batch(True, last.blocks + (call,))
        else:
            batches.append(Batch(safe, (call,)))
    return tuple(batches)


def run_tools(
    calls: Sequence[ToolCall],
    context: ToolUseContext,
    can_use_tool: CanUseToolFn,
    parent_message: ChatMessage | None,
    hooks: HookRunner,
) -> tuple[ToolUseOutcome, ...]:
    """跑完一轮的全部调用, 按请求序返回."""
    results: dict[str, ToolUseOutcome] = {}
    for batch in partition_tool_calls(calls, context.options.tools):
        if batch.is_concurrency_safe and len(batch.blocks) > 1:
            _run_concurrently(
                batch, context, can_use_tool, parent_message, hooks, results
            )
        else:
            for call in batch.blocks:
                results[call.tool_call_id] = run_tool_use(
                    call, context, can_use_tool, parent_message, hooks
                )
    return tuple(results[call.tool_call_id] for call in calls)


def _run_concurrently(
    batch: Batch,
    context: ToolUseContext,
    can_use_tool: CanUseToolFn,
    parent_message: ChatMessage | None,
    hooks: HookRunner,
    results: dict[str, ToolUseOutcome],
) -> None:
    workers = min(max_tool_use_concurrency(), len(batch.blocks))
    _log.debug("tools.concurrent_batch", size=len(batch.blocks), workers=workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        for call in batch.blocks:
            futures.append(
                (
                    call,
                    pool.submit(
                        run_tool_use, call, context, can_use_tool, parent_message, hooks
                    ),
                )
            )
        for call, future in futures:
            results[call.tool_call_id] = future.result()
