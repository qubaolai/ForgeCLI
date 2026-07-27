"""AgentLoop 契约冻结自洽（2026-07-23，ADR-0010 §4/§6/§7）。

只验证「契约形状 + 字段不变量 + 边界依赖」，不涉及循环体（BuiltinAgentLoop 属
2026-07-24）。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from forgecli.application.agent_loop import (
    AgentLoop,
    AnswerAction,
    ApprovalRequest,
    ApprovalRequestAction,
    AskUserAction,
    CompactionRequestAction,
    ContextPackage,
    HookResult,
    LoopAction,
    LoopBudgets,
    LoopDecision,
    LoopEvent,
    LoopEventBus,
    LoopEventKind,
    LoopEventSubscriber,
    LoopHook,
    LoopInput,
    LoopObservation,
    LoopState,
    LoopStop,
    LoopStopReason,
    ModePolicy,
    StopClassification,
    ToolRequest,
    ToolRequestAction,
)
from forgecli.domain.intents import SessionMode, UserMessage

# ---- LoopStopReason 全集与分类（§6）----


def test_stop_reason_covers_full_set() -> None:
    values = {r.value for r in LoopStopReason}
    assert values == {
        "final_answer",
        "wait_user_input",
        "wait_approval",
        "user_cancelled",
        "budget_exhausted",
        "policy_denied",
        "context_compaction_required",
        "tool_failed_blocking",
        "model_error_blocking",
        "session_interrupted",
        "max_retry_exceeded",
    }


def test_stop_reason_classification_matches_adr() -> None:
    assert LoopStopReason.FINAL_ANSWER.classification is StopClassification.NORMAL
    for reason in (
        LoopStopReason.WAIT_USER_INPUT,
        LoopStopReason.WAIT_APPROVAL,
        LoopStopReason.CONTEXT_COMPACTION_REQUIRED,
        LoopStopReason.SESSION_INTERRUPTED,
    ):
        assert reason.is_resumable_pause
    for reason in (
        LoopStopReason.POLICY_DENIED,
        LoopStopReason.TOOL_FAILED_BLOCKING,
        LoopStopReason.MODEL_ERROR_BLOCKING,
        LoopStopReason.MAX_RETRY_EXCEEDED,
        LoopStopReason.USER_CANCELLED,
    ):
        assert reason.classification is StopClassification.BLOCKING
    assert (
        LoopStopReason.BUDGET_EXHAUSTED.classification
        is StopClassification.POLICY_DEPENDENT
    )


def test_loop_stop_of_derives_resumable_from_classification() -> None:
    assert LoopStop.of(LoopStopReason.WAIT_APPROVAL).resumable is True
    assert LoopStop.of(LoopStopReason.POLICY_DENIED).resumable is False
    # 显式覆盖（如预算策略裁定 BUDGET_EXHAUSTED 可恢复）以传入为准。
    assert (
        LoopStop.of(LoopStopReason.BUDGET_EXHAUSTED, resumable=True).resumable is True
    )


# ---- LoopAction 密封子类型（§4.3）----


def test_loop_action_variants_are_sealed_subtypes() -> None:
    actions = [
        AnswerAction(text="done"),
        ToolRequestAction(request=ToolRequest(name="read_file")),
        AskUserAction(prompt="which file?"),
        ApprovalRequestAction(
            request=ApprovalRequest(action_summary="rm build/", risk_summary="可回滚")
        ),
        CompactionRequestAction(),
    ]
    for action in actions:
        assert isinstance(action, LoopAction)


def test_tool_request_rejects_blank_name() -> None:
    with pytest.raises(ValueError):
        ToolRequest(name="  ")


def test_ask_user_rejects_blank_prompt() -> None:
    with pytest.raises(ValueError):
        AskUserAction(prompt="  ")


def test_approval_request_requires_both_summaries() -> None:
    with pytest.raises(ValueError):
        ApprovalRequest(action_summary="", risk_summary="x")
    with pytest.raises(ValueError):
        ApprovalRequest(action_summary="x", risk_summary="")


def test_loop_decision_defaults_are_empty() -> None:
    decision = LoopDecision()
    assert decision.reason_summary is None
    assert decision.next_action is None
    assert decision.continue_reason is None


# ---- LoopInput / LoopState 字段不变量（§4.1 / §4.2 / §12）----


def _mode_policy(mode: SessionMode = SessionMode.ACCEPT_EDITS) -> ModePolicy:
    return ModePolicy(mode=mode)


def _loop_input(mode: SessionMode = SessionMode.ACCEPT_EDITS) -> LoopInput:
    return LoopInput(
        turn_id="turn_0001",
        session_id="sess_1",
        user_intent=UserMessage(raw_text="hi"),
        mode=mode,
        mode_policy=_mode_policy(mode),
    )


def test_loop_input_valid_construction() -> None:
    loop_input = _loop_input()
    assert loop_input.tool_catalog == ()
    assert loop_input.resume_state is None
    assert isinstance(loop_input.context_package, ContextPackage)
    assert isinstance(loop_input.budgets, LoopBudgets)


def test_loop_input_mode_must_match_policy() -> None:
    with pytest.raises(ValueError):
        LoopInput(
            turn_id="turn_0001",
            session_id="sess_1",
            user_intent=UserMessage(raw_text="hi"),
            mode=SessionMode.AUTO,
            mode_policy=_mode_policy(SessionMode.PLAN),
        )


def test_loop_input_rejects_blank_ids() -> None:
    with pytest.raises(ValueError):
        LoopInput(
            turn_id="  ",
            session_id="sess_1",
            user_intent=UserMessage(raw_text="hi"),
            mode=SessionMode.PLAN,
            mode_policy=_mode_policy(SessionMode.PLAN),
        )


def test_loop_state_defaults_and_validation() -> None:
    state = LoopState(turn_id="turn_0001")
    assert state.step_index == 0
    assert state.observations == ()
    assert state.pending_actions == ()
    with pytest.raises(ValueError):
        LoopState(turn_id="turn_0001", step_index=-1)


def test_loop_budgets_reject_negative() -> None:
    LoopBudgets()  # 全 None 合法
    with pytest.raises(ValueError):
        LoopBudgets(max_steps_per_turn=-1)


def test_resume_state_carries_loop_state() -> None:
    state = LoopState(turn_id="turn_0001", step_index=3)
    loop_input = LoopInput(
        turn_id="turn_0001",
        session_id="sess_1",
        user_intent=UserMessage(raw_text="continue"),
        mode=SessionMode.ACCEPT_EDITS,
        mode_policy=_mode_policy(),
        resume_state=state,
    )
    assert loop_input.resume_state is state


# ---- HookResult / LoopHook（§7.1）----


def test_hook_result_stop_requires_reason() -> None:
    with pytest.raises(ValueError):
        HookResult(should_continue=False)
    stop = HookResult.stop(LoopStopReason.BUDGET_EXHAUSTED)
    assert stop.should_continue is False
    assert stop.stop_reason is LoopStopReason.BUDGET_EXHAUSTED


def test_hook_result_constructors() -> None:
    assert HookResult.proceed().should_continue is True
    state = LoopState(turn_id="turn_0001")
    assert HookResult.mutate(state=state).mutated_state is state


def test_default_hook_passes_through_all_timepoints() -> None:
    hook = LoopHook()
    state = LoopState(turn_id="turn_0001")
    assert hook.before_step(state).should_continue is True
    assert hook.before_action(AnswerAction(text="x"), state).should_continue is True
    obs = LoopObservation(content="ok")
    assert hook.after_action(obs, state).should_continue is True


# ---- LoopEvent / LoopEventBus（§7.2）----


def test_event_bus_dispatches_in_order() -> None:
    seen: list[str] = []

    class Recorder(LoopEventSubscriber):
        def __init__(self, tag: str) -> None:
            self.tag = tag

        def on_event(self, event: LoopEvent) -> None:
            seen.append(f"{self.tag}:{event.kind.value}")

    bus = LoopEventBus()
    bus.subscribe(Recorder("a"))
    bus.subscribe(Recorder("b"))
    bus.publish(LoopEvent(kind=LoopEventKind.LOOP_STARTED, turn_id="turn_0001"))
    assert seen == ["a:loop_started", "b:loop_started"]


def test_event_bus_isolates_subscriber_failure() -> None:
    seen: list[str] = []

    class Boom(LoopEventSubscriber):
        def on_event(self, event: LoopEvent) -> None:
            raise RuntimeError("subscriber blew up")

    class Good(LoopEventSubscriber):
        def on_event(self, event: LoopEvent) -> None:
            seen.append(event.kind.value)

    bus = LoopEventBus()
    bus.subscribe(Boom())
    bus.subscribe(Good())
    # 坏订阅者不打断其余分发，也不向上抛。
    bus.publish(LoopEvent(kind=LoopEventKind.STEP_STARTED, turn_id="turn_0001"))
    assert seen == ["step_started"]
    assert len(bus.isolated_failures) == 1
    assert isinstance(bus.isolated_failures[0][2], RuntimeError)


# ---- 端口与边界（§3 / §14）----


def test_agent_loop_is_abstract() -> None:
    assert inspect.isabstract(AgentLoop)
    assert set(AgentLoop.__abstractmethods__) == {"start", "observe"}


def test_agent_loop_package_has_no_forbidden_imports() -> None:
    """§14：AgentLoop 不 import CLI / filesystem / shell / git / 具体 LLM SDK。"""
    pkg_dir = Path(inspect.getfile(AgentLoop)).parent
    forbidden = (
        "import subprocess",
        "import os",
        "from forgecli.interfaces",
        "from forgecli.infrastructure",
        "import openai",
        "import httpx",
    )
    for path in pkg_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{path.name} 不应包含 `{needle}`"
