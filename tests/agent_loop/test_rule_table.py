"""规则表: 顺序校验与异常隔离 (ADR-0049 决策 3)."""

from __future__ import annotations

import pytest

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_loop.rule import LoopRuleBase, LoopView, OrderRule
from forgecli.application.agent_loop.rule_table import (
    RuleOrderError,
    validate_rule_order,
)
from forgecli.application.agent_loop.rules import builtin_rules
from forgecli.application.agent_loop.rules.context_fit import ContextFitRule
from forgecli.application.agent_loop.rules.model_budget import ModelBudgetRule
from forgecli.application.agent_run.events import AgentRunEventKind as K
from forgecli.application.llm.transport_policy import ModelTransportPolicy
from forgecli.domain.agent.stop import LoopStopReason
from support.loop_fakes import FakeMeter, Reply, loop_input, recording_bus


def test_builtin_table_is_consistent():
    validate_rule_order(builtin_rules(context=None, workspace_provider=None))


def test_swapping_budget_and_fit_fails_with_reason():
    rules = (ContextFitRule(None), ModelBudgetRule())
    with pytest.raises(RuleOrderError) as caught:
        validate_rule_order(rules)
    message = str(caught.value)
    assert "model_budget" in message and "context_fit" in message
    assert "压缩还要再调一次模型" in message


def test_unknown_peer_is_an_error():
    class Lonely(LoopRuleBase):
        name = "lonely"
        runs_before = (OrderRule("nobody", "x"),)

    with pytest.raises(RuleOrderError, match="表里没有它"):
        validate_rule_order((Lonely(),))


def test_duplicate_names_are_an_error():
    class A(LoopRuleBase):
        name = "same"

    with pytest.raises(RuleOrderError, match="重复"):
        validate_rule_order((A(), A()))


def test_order_rule_requires_reason():
    with pytest.raises(ValueError):
        OrderRule("peer", " ")


def test_broken_rule_does_not_fail_the_turn():
    class Broken(LoopRuleBase):
        name = "broken"

        def before_model(self, view: LoopView):
            raise RuntimeError("boom")

    bus, events = recording_bus()
    from support.loop_fakes import ScriptedGateway

    loop = BuiltinAgentLoop(
        ScriptedGateway(Reply(text="ok")),
        FakeMeter(),  # type: ignore[arg-type]
        rules=(Broken(), *builtin_rules(context=None, workspace_provider=None)),
        model_transport_policy=ModelTransportPolicy(),
        event_bus=bus,
    )
    from forgecli.domain.agent.actions import AnswerAction, LoopObservation

    step = loop.start(loop_input())
    assert isinstance(step, AnswerAction)
    stop = loop.observe(LoopObservation(content="answer_delivered"))
    assert stop.reason is LoopStopReason.FINAL_ANSWER
    summaries = [
        e.payload.reason_summary  # type: ignore[attr-defined]
        for e in events.of(K.DECISION_SUMMARY)
    ]
    assert any("broken" in s and "before_model" in s for s in summaries)
    assert events.kinds()[-1] is K.TURN_COMPLETED


def test_fit_before_workspace_watch_fails_with_reason():
    """ADR-0049 验收: 把 context_fit 挪到 workspace_watch 之前, 装配时抛错并带理由."""
    from forgecli.application.agent_loop.rules.workspace_watch import (
        WorkspaceWatchRule,
    )

    rules = (ContextFitRule(None), WorkspaceWatchRule(None))
    with pytest.raises(RuleOrderError) as caught:
        validate_rule_order(rules)
    assert "workspace_watch 必须排在 context_fit 之前" in str(caught.value)
    assert "通知要先进窗口" in str(caught.value)
