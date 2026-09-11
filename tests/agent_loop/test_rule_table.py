"""规则表: 内置表的顺序, 以及规则出错时的隔离 (ADR-0049 决策 3, 2026-09-11 修订)."""

from __future__ import annotations

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_loop.rule import LoopView
from forgecli.application.agent_loop.rule_table import RuleTable
from forgecli.application.agent_loop.rules import builtin_rules
from forgecli.application.agent_run.events import AgentRunEventKind as K
from forgecli.application.llm.transport_policy import ModelTransportPolicy
from forgecli.domain.agent.actions import AnswerAction, LoopObservation
from forgecli.domain.agent.stop import LoopStopReason
from support.loop_fakes import (
    FakeMeter,
    Reply,
    ScriptedGateway,
    loop_input,
    recording_bus,
)


def names(steps) -> list[str]:
    return [step.__qualname__ for step in steps]


def test_builtin_order_before_model():
    """工作区通知与预算都要在压缩之前.

    通知先进窗口, 压缩才把它算进预算; 该停了就别再为压缩花一次模型调用.
    """
    table = builtin_rules(context=None, workspace_provider=None)
    assert names(table.before_model) == [
        "WorkspaceWatchRule.before_model",
        "ModelBudgetRule.before_model",
        "ContextFitRule.before_model",
    ]


def test_builtin_order_after_model():
    """格式坏掉在目录已收之前: 一个格式坏掉的调用不该被当成"无视目录已收"的证据."""
    table = builtin_rules(context=None, workspace_provider=None)
    assert names(table.after_model) == [
        "WorkspaceWatchRule.after_model",
        "EmptyResponseRule.after_model",
        "MalformedOutputRule.after_model",
        "ToolsClosedRule.after_model",
    ]


def test_builtin_order_after_observe():
    """计划评审在收工具之前: 一个是"轮到人说话", 一个是"你被罚闭嘴"."""
    table = builtin_rules(context=None, workspace_provider=None)
    assert names(table.after_observe) == [
        "WorkspaceWatchRule.after_observe",
        "PlanReviewRule.after_observe",
        "RefusalRule.after_observe",
        "BarrenStreakRule.after_observe",
    ]


def test_builtin_order_error_and_dispatch():
    table = builtin_rules(context=None, workspace_provider=None)
    assert names(table.on_model_error) == [
        "ContextFitRule.on_overflow",
        "MalformedOutputRule.on_bad_json",
    ]
    assert names(table.before_dispatch) == [
        "WorkspaceWatchRule.before_dispatch",
        "RepeatCallRule.before_dispatch",
    ]


def test_same_object_shares_state_across_timings():
    """挂了几个时机的规则在表里是同一个对象, 不是几个副本."""
    table = builtin_rules(context=None, workspace_provider=None)
    owners = {
        timing: {getattr(step, "__self__", None) for step in steps}
        for timing, steps in (
            ("before_model", table.before_model),
            ("after_model", table.after_model),
            ("before_dispatch", table.before_dispatch),
            ("after_observe", table.after_observe),
        )
    }
    workspace = next(
        s for s in owners["before_model"] if type(s).__name__ == "WorkspaceWatchRule"
    )
    for timing in ("after_model", "before_dispatch", "after_observe"):
        assert workspace in owners[timing], timing


def test_describe_lists_every_timing():
    table = builtin_rules(context=None, workspace_provider=None)
    described = table.describe()
    assert set(described) == {
        "before_model",
        "on_model_error",
        "after_model",
        "before_dispatch",
        "after_observe",
    }
    assert described["before_dispatch"] == names(table.before_dispatch)


def test_broken_rule_does_not_fail_the_turn():
    def broken(view: LoopView):
        raise RuntimeError("boom")

    builtin = builtin_rules(context=None, workspace_provider=None)
    table = RuleTable(
        before_model=(broken, *builtin.before_model),
        on_model_error=builtin.on_model_error,
        after_model=builtin.after_model,
        before_dispatch=builtin.before_dispatch,
        after_observe=builtin.after_observe,
    )
    bus, events = recording_bus()
    loop = BuiltinAgentLoop(
        ScriptedGateway(Reply(text="ok")),
        FakeMeter(),  # type: ignore[arg-type]
        rules=table,
        model_transport_policy=ModelTransportPolicy(),
        event_bus=bus,
    )
    step = loop.start(loop_input())
    assert isinstance(step, AnswerAction)
    stop = loop.observe(LoopObservation(content="answer_delivered"))
    assert stop.reason is LoopStopReason.FINAL_ANSWER
    summaries = [
        e.payload.reason_summary  # type: ignore[attr-defined]
        for e in events.of(K.DECISION_SUMMARY)
    ]
    assert any("broken" in s and "已跳过" in s for s in summaries)
    assert events.kinds()[-1] is K.TURN_COMPLETED
