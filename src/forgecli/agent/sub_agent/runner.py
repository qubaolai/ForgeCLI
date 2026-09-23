"""Bounded, joined child runs. A timeout requests cancellation, never detaches."""

from __future__ import annotations

import math
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from forgecli.agent.loop.builtin_loop import BuiltinAgentLoop
from forgecli.agent.run_bus import AgentRunEventBus, AgentRunEventSubscriber
from forgecli.agent.run_events import (
    AgentRunEvent,
    AgentRunEventKind,
    RunEventPayload,
    SubAgentFinishedPayload,
    SubAgentStartedPayload,
)
from forgecli.common.cancellation import CancelledError, CancelToken
from forgecli.common.json_io import write_document
from forgecli.common.observability.context import bind
from forgecli.common.observability.log import get_log
from forgecli.core.agent.actions import LoopResult, LoopStop
from forgecli.core.agent.state import AssembledContext, LoopInput
from forgecli.core.agent.stop import LoopStopReason
from forgecli.core.agent.sub_agent import (
    SubAgentIdentity,
    SubAgentLimitError,
    SubAgentReport,
    SubAgentStatus,
    SubAgentTask,
)
from forgecli.core.context.window import Window
from forgecli.core.message import ChatMessage, TextBlock
from forgecli.core.model.usage import UsageRecordDraft
from forgecli.core.permissions import PermissionMode
from forgecli.core.serialization import to_jsonable
from forgecli.core.tool.tool import ToolUseContext
from forgecli.core.turn import MessageRole
from forgecli.prompt import prompts
from forgecli.tools.runner import ToolRunner, observation_of

_log = get_log(__name__)


def positive_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


@dataclass(frozen=True)
class SubAgentResources:
    loop: BuiltinAgentLoop
    tools: ToolRunner
    context: AssembledContext
    diagnostic_file: Path
    record_usage: Callable[[UsageRecordDraft], None]


class SubAgentRunner(AgentRunEventSubscriber):
    def __init__(
        self,
        *,
        bus: AgentRunEventBus,
        prepare: Callable[[SubAgentIdentity, CancelToken, int], SubAgentResources],
        concurrency: int = 1,
        limit: int = 8,
        model_calls: int = 30,
        timeout: float = 300,
        grace: float = 30,
    ) -> None:
        if min(concurrency, limit, model_calls, timeout, grace) <= 0 or not all(
            math.isfinite(v) for v in (timeout, grace)
        ):
            raise ValueError("Sub-agent limits must be positive")
        self._bus = bus
        self._prepare = prepare
        self._slots = threading.BoundedSemaphore(concurrency)
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str], int] = {}
        self._limit = limit
        self._model_calls = model_calls
        self._timeout = timeout
        self._grace = grace

    def on_event(self, event: AgentRunEvent) -> None:
        if not event.agent_id and event.kind in (
            AgentRunEventKind.TURN_COMPLETED,
            AgentRunEventKind.TURN_CANCELLED,
            AgentRunEventKind.TURN_FAILED,
        ):
            with self._lock:
                self._counts.pop((event.session_id, event.turn_id), None)

    def run(self, task: SubAgentTask, context: ToolUseContext) -> SubAgentReport:
        if context.abort.cancelled:
            raise CancelledError()
        key = (context.session_id, context.turn_id)
        with self._lock:
            count = self._counts.get(key, 0)
            if count >= self._limit:
                raise SubAgentLimitError(prompts.sub_agent_limit(self._limit))
            self._counts[key] = count + 1
        while not self._slots.acquire(timeout=0.1):
            if context.abort.cancelled:
                raise CancelledError()
        try:
            if context.abort.cancelled:
                raise CancelledError()
            identity = SubAgentIdentity(
                *key, f"agent_{uuid.uuid4().hex[:12]}", context.tool_use_id
            )
            return self._run(task, context.abort, identity)
        finally:
            self._slots.release()

    def _run(
        self, task: SubAgentTask, parent: CancelToken, identity: SubAgentIdentity
    ) -> SubAgentReport:
        started = time.monotonic()
        child = parent.child()
        resources = self._prepare(identity, child, self._model_calls)
        results: list[LoopResult] = []
        errors: list[str] = []

        def publish(kind: AgentRunEventKind, payload: RunEventPayload) -> None:
            self._bus.publish(
                kind,
                session_id=identity.session_id,
                turn_id=identity.turn_id,
                agent_id=identity.agent_id,
                payload=payload,
            )

        def work() -> None:
            with bind(
                session_id=identity.session_id,
                turn_id=identity.turn_id,
                agent_id=identity.agent_id,
            ):
                publish(
                    AgentRunEventKind.SUB_AGENT_STARTED,
                    SubAgentStartedPayload(
                        task.description, identity.parent_tool_call_id
                    ),
                )
                loop = resources.loop
                messages: tuple[ChatMessage, ...] = ()

                def assemble(window: Window) -> AssembledContext:
                    nonlocal messages
                    messages = window.messages
                    return resources.context

                try:
                    results.append(
                        loop.run(
                            LoopInput(
                                session_id=identity.session_id,
                                turn_id=identity.turn_id,
                                agent_id=identity.agent_id,
                                mode=PermissionMode.DEFAULT,
                                initial_window=(
                                    ChatMessage(
                                        role=MessageRole.USER,
                                        content=(
                                            TextBlock(
                                                prompts.sub_agent_task(task.prompt)
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                            assemble=assemble,
                            execute=lambda calls: tuple(
                                observation_of(outcome)
                                for outcome in resources.tools.run(
                                    calls,
                                    session_id=identity.session_id,
                                    turn_id=identity.turn_id,
                                    cancel=child,
                                    messages=messages,
                                )
                            ),
                        )
                    )
                except BaseException as error:
                    _log.exception("sub_agent.failed", agent_id=identity.agent_id)
                    errors.append(f"{type(error).__name__}: {error}")
                    results.append(
                        loop.exit_snapshot(
                            LoopStop(LoopStopReason.MODEL_ERROR_BLOCKING, str(error))
                        )
                    )

        thread = threading.Thread(
            target=work, name=f"forge-agent-{identity.agent_id}", daemon=False
        )
        if parent.cancelled:
            raise CancelledError()
        timed_out = time.monotonic() - started >= self._timeout
        if timed_out:
            child.cancel()
        thread.start()
        stopping_since: float | None = None
        warned = False
        while thread.is_alive():
            if time.monotonic() - started >= self._timeout:
                timed_out = True
                child.cancel()
            if child.cancelled:
                stopping_since = stopping_since or time.monotonic()
                if not warned and time.monotonic() - stopping_since >= self._grace:
                    warned = True
                    _log.warning(
                        "sub_agent.cleanup_delayed", agent_id=identity.agent_id
                    )
                    publish(AgentRunEventKind.SUB_AGENT_STOPPING, RunEventPayload())
            thread.join(0.05)
        result = results[0]
        status = self._status(parent, timed_out, result)
        drafts = tuple(
            {draft.request_id: draft for draft in result.usage_drafts}.values()
        )
        usage_complete = (
            not errors
            and result.stop.reason
            not in (LoopStopReason.USER_CANCELLED, LoopStopReason.MODEL_ERROR_BLOCKING)
            and sum(d.origin.value == "sub_agent" for d in drafts) == result.model_calls
        )
        for draft in drafts:
            try:
                resources.record_usage(draft)
            except Exception as error:
                errors.append(f"Usage persistence failed: {error}")
                usage_complete = False
                break  # Never replay drafts after a partial write.
        if parent.cancelled:
            status = SubAgentStatus.CANCELLED
        if errors and status is not SubAgentStatus.CANCELLED:
            status = SubAgentStatus.FAILED
        duration = (time.monotonic() - started) * 1000
        body = result.answer or result.partial_answer or result.stop.message or ""
        if status is not SubAgentStatus.COMPLETED:
            body += "\n" + prompts.sub_agent_stopped(status.value)
        if errors:
            body += "\n" + "\n".join(errors)
        total = sum(
            d.usage.total_tokens or d.usage.input_tokens + d.usage.output_tokens
            for d in drafts
        )
        try:
            write_document(
                resources.diagnostic_file,
                {
                    "identity": to_jsonable(identity),
                    "status": status.value,
                    "window": to_jsonable(result.window),
                    "usage": [d.to_payload() for d in drafts],
                    "usage_complete": usage_complete,
                    "errors": errors,
                },
            )
        except Exception as error:
            _log.exception("sub_agent.snapshot_failed", agent_id=identity.agent_id)
            body += f"\nSnapshot persistence failed: {error}"
            if status is not SubAgentStatus.CANCELLED:
                status = SubAgentStatus.FAILED
        if parent.cancelled:
            status = SubAgentStatus.CANCELLED
        body += prompts.sub_agent_trailer(
            identity.agent_id,
            status.value,
            result.model_calls,
            result.tool_calls,
            total,
            duration,
            usage_complete,
        )
        publish(
            AgentRunEventKind.SUB_AGENT_FINISHED,
            SubAgentFinishedPayload(
                status.value,
                result.model_calls,
                result.tool_calls,
                total,
                duration,
                detail=body[-2000:],
                usage_complete=usage_complete,
            ),
        )
        if parent.cancelled:
            raise CancelledError()
        return SubAgentReport(
            identity,
            status,
            body,
            result.model_calls,
            result.tool_calls,
            drafts,
            duration,
            usage_complete,
        )

    @staticmethod
    def _status(
        parent: CancelToken, timed_out: bool, result: LoopResult
    ) -> SubAgentStatus:
        if parent.cancelled:
            return SubAgentStatus.CANCELLED
        if timed_out:
            return SubAgentStatus.TIMED_OUT
        return {
            LoopStopReason.FINAL_ANSWER: SubAgentStatus.COMPLETED,
            LoopStopReason.BUDGET_EXHAUSTED: SubAgentStatus.BUDGET_EXHAUSTED,
        }.get(result.stop.reason, SubAgentStatus.FAILED)
