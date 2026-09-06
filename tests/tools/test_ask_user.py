"""ask_user 的判据 (ADR-0043).

三件事必须钉住:

1. **回答就是这次调用的 ToolResult** —— 于是"回答关联原来的 tool_call_id"不是一条要靠
   谁维护的约束, 而是数据流的形状.
2. **没拿到回答不停下循环** —— 与审批的 HALT 刻意相反: 那里没人回答意味着不能执行,
   这里只意味着模型得自己拿主意.
3. **提问不产生任何授权** —— 它只声明 USER_PROMPT, 而那一档不在 MUTATING_CAPABILITIES
   里, 也永远不落到 ASK.
"""

from __future__ import annotations

import threading
import time
from typing import cast

import pytest

from forgecli.application.human_prompt import (
    MAX_QUESTIONS_PER_TURN,
    PendingHumanPromptService,
)
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.tool_request.catalog_predicates import catalog_query_for_mode
from forgecli.application.tool_request.observations import ObservationKind
from forgecli.application.tools.builtin.ask_user import AskUserTool
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.agent.actions import ObservationDisposition
from forgecli.domain.execution.fence import fence_for
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.budget import capabilities_requiring_approval
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.findings import AnalysisFindings
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.capability import MUTATING_CAPABILITIES, Capability
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    ExecutionContextRef,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.domain.tool.result import ToolResultStatus
from forgecli.interfaces.runtime.human_prompt import BlockingHumanPromptBroker
from forgecli.shared.cancellation import CancelToken

_MODES = (
    "read_only/always",
    "workspace_write/always",
    "workspace_write/auto",
    "full_access/never",
)

# perform 从头到尾没碰过 context —— 那正是它的 PlanEffects 为空的原因. 传 None 让这件事
# 在用例里也是可见的: 哪天它开始读 context, 这里会立刻炸.
_NO_CONTEXT = cast(ExecutionContext, None)


def _plan(**arguments: object) -> ToolPlan:
    payload: dict[str, object] = {"question": "软删除用哪一种?"}
    payload.update(arguments)
    return ToolPlan(
        plan_id="inv-1",
        tool_name="ask_user",
        spec_hash=AskUserTool(PendingHumanPromptService()).spec.spec_hash,
        normalized_input=payload,
        capabilities=frozenset({Capability.USER_PROMPT}),
        effects=PlanEffects(),
        target_resolution=TargetResolution.STATIC,
        workspace_scope=WorkspaceScope.IN_WORKSPACE,
        execution_context=ExecutionContextRef(cwd="/ws", environment_hash="e"),
        declaration_confidence=DeclarationConfidence.DECLARED,
    )


_OPTIONS = [
    {"value": "deleted_at", "label": "deleted_at 时间戳"},
    {"value": "is_deleted", "label": "is_deleted 布尔"},
]


def _ask_in_background(
    tool: AskUserTool, plan: ToolPlan, cancel: CancelToken | None = None
) -> list:
    out: list = []
    thread = threading.Thread(
        target=lambda: out.append(tool.perform(plan, _NO_CONTEXT, cancel))
    )
    thread.start()
    return [thread, out]


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("等待超时")


# ---- 回答就是这次调用的结果 ----


def test_the_answer_becomes_this_calls_result() -> None:
    broker = BlockingHumanPromptBroker()
    tool = AskUserTool(broker)
    plan = _plan()
    thread, out = _ask_in_background(tool, plan)
    _wait_until(lambda: bool(broker.list_pending()))
    assert broker.resolve("inv-1", "", "都不对, 用 status 字段") is True
    thread.join(timeout=2)

    result = out[0]
    assert result.status is ToolResultStatus.OK
    # invocation_id 就是 plan_id, 而协调器据此把结果配回那一次 tool_call.
    assert result.invocation_id == "inv-1"
    assert "status 字段" in result.raw_output()


def test_a_picked_option_comes_back_as_a_readable_line() -> None:
    """回 label 而不是 value: 两者都是模型自己写的, 但 label 读起来是一句话."""
    broker = BlockingHumanPromptBroker()
    tool = AskUserTool(broker)
    thread, out = _ask_in_background(tool, _plan(options=_OPTIONS))
    _wait_until(lambda: bool(broker.list_pending()))
    assert broker.resolve("inv-1", "deleted_at") is True
    thread.join(timeout=2)

    result = out[0]
    assert result.status is ToolResultStatus.OK
    assert result.data == {
        "status": "answered",
        "selected_values": ["deleted_at"],
        "text": "",
    }
    assert "deleted_at 时间戳" in result.raw_output()


def test_free_text_is_offered_even_when_options_exist() -> None:
    """选项是建议, 不是限制 (ADR-0043 决策 7)."""
    broker = BlockingHumanPromptBroker()
    tool = AskUserTool(broker)
    thread, out = _ask_in_background(tool, _plan(options=_OPTIONS))
    _wait_until(lambda: bool(broker.list_pending()))
    assert broker.list_pending()[0]["free_text"] is True
    assert broker.resolve("inv-1", "", "都不对") is True
    thread.join(timeout=2)
    assert "都不对" in out[0].raw_output()


def test_the_question_reaches_the_card_as_the_title() -> None:
    broker = BlockingHumanPromptBroker()
    tool = AskUserTool(broker)
    thread, _ = _ask_in_background(tool, _plan())
    _wait_until(lambda: bool(broker.list_pending()))
    pending = broker.list_pending()[0]
    assert pending["kind"] == "question"
    assert pending["title"] == "软删除用哪一种?"
    broker.resolve("inv-1", "", "随便")
    thread.join(timeout=2)


# ---- 没拿到回答不停下循环 ----


def test_no_one_to_answer_does_not_stop_the_loop() -> None:
    """与 APPROVAL_UNAVAILABLE 的 HALT 刻意相反 (ADR-0043 决策 8).

    审批那里没人回答意味着不能执行, 继续派工具没有意义; 提问这里只意味着模型得自己拿
    主意, 而那本来就是它绝大多数时候在做的事.
    """
    result = AskUserTool(PendingHumanPromptService()).perform(_plan(), _NO_CONTEXT)
    assert result.status is ToolResultStatus.UNAVAILABLE
    # 它照常是一条 TOOL_RESULT, 而那一类的分流是 CONTINUE.
    assert ObservationKind.TOOL_RESULT.disposition is ObservationDisposition.CONTINUE
    assert "按你自己的判断继续" in result.raw_output()


def test_a_cancelled_wait_is_reported_as_cancelled() -> None:
    broker = BlockingHumanPromptBroker()
    tool = AskUserTool(broker)
    cancel = CancelToken()
    thread, out = _ask_in_background(tool, _plan(), cancel)
    _wait_until(lambda: bool(broker.list_pending()))
    cancel.cancel()
    broker.release_pending("用户停止了这一轮")
    thread.join(timeout=2)
    assert out[0].status is ToolResultStatus.CANCELLED


def test_the_question_budget_shows_up_as_a_result_not_a_hang() -> None:
    broker = BlockingHumanPromptBroker()
    tool = AskUserTool(broker)
    for _ in range(MAX_QUESTIONS_PER_TURN):
        thread, _ = _ask_in_background(tool, _plan())
        _wait_until(lambda: bool(broker.list_pending()))
        broker.resolve("inv-1", "", "好")
        thread.join(timeout=2)

    result = tool.perform(_plan(), _NO_CONTEXT)
    assert result.status is ToolResultStatus.UNAVAILABLE
    assert str(MAX_QUESTIONS_PER_TURN) in result.summary


# ---- 提问不产生任何授权 ----


def test_asking_is_not_a_mutating_capability() -> None:
    assert Capability.USER_PROMPT not in MUTATING_CAPABILITIES


@pytest.mark.parametrize("mode", _MODES)
def test_asking_never_falls_back_to_approval(mode: str) -> None:
    """为"要不要问你一个问题"再问一次人是一个字面意义上的死循环."""
    session_mode = SessionMode.from_value(mode)
    assert (
        capabilities_requiring_approval(
            frozenset({Capability.USER_PROMPT}),
            fence_for(session_mode, workspace_roots=("/ws",)),
            confined=False,
            targets_closed=False,
        )
        == frozenset()
    )


def test_the_engine_allows_it_by_its_own_fast_path() -> None:
    """审计要答得出"这次为什么没问人": 一个专门的理由码, 不是搭了别人的便车."""
    findings = AnalysisFindings(plan=_plan())
    decision = PolicyEngine().decide(
        findings,
        PolicyContext(
            mode=SessionMode.from_value("read_only/always"),
            session_id="s",
            turn_id="t",
            execution_profile_hash="profile",
        ),
    )
    assert decision.decision is Decision.ALLOW
    assert decision.reason is DecisionReason.USER_PROMPT_FAST_PATH


def test_ask_user_stays_visible_in_the_read_only_catalog() -> None:
    """plan 档正是最需要问清方向的一档."""
    spec = AskUserTool(PendingHumanPromptService()).spec
    query = catalog_query_for_mode(SessionMode.from_value("read_only/always"))
    assert query.predicate(spec) is True


def test_the_tool_declares_nothing_but_asking() -> None:
    spec = AskUserTool(PendingHumanPromptService()).spec
    assert spec.declared_capabilities == frozenset({Capability.USER_PROMPT})
    # 没有路径字段, 且不收额外字段 —— 目标集合在机制上就是封闭的.
    assert spec.input_schema["additionalProperties"] is False


@pytest.mark.parametrize("skipped", [False, True])
def test_structured_multiple_answer_and_skip(skipped: bool) -> None:
    broker = BlockingHumanPromptBroker()
    tool = AskUserTool(broker)
    thread, out = _ask_in_background(
        tool,
        _plan(
            options=_OPTIONS,
            selection_mode="multiple",
            recommended_option_id="deleted_at",
        ),
    )
    try:
        _wait_until(lambda: bool(broker.list_pending()))
        pending = broker.list_pending()[0]
        assert pending["selection_mode"] == "multiple"
        assert pending["recommended_option_id"] == "deleted_at"
        assert pending["allow_skip"] is True
        selected = () if skipped else ("deleted_at", "is_deleted")
        assert broker.resolve(
            "inv-1",
            selected_values=selected,
            text="" if skipped else "补充",
            skipped=skipped,
        )
        thread.join(timeout=2)
        assert out[0].data == {
            "status": "skipped" if skipped else "answered",
            "selected_values": list(selected),
            "text": "" if skipped else "补充",
        }
        assert out[0].status is ToolResultStatus.OK
        if skipped:
            assert "不要视为同意推荐项" in out[0].raw_output()
        else:
            assert "deleted_at 时间戳" in out[0].raw_output()
            assert "is_deleted 布尔" in out[0].raw_output()
            assert "补充" in out[0].raw_output()
    finally:
        broker.close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "patch",
    [
        {"question": " "},
        {"selection_mode": "both"},
        {"recommended_option_id": "missing"},
        {"recommended_option_id": ["a", "b"]},
        {"options": [{"value": "a", "label": "A"}]},
        {"options": [{"value": " ", "label": "A", "detail": "说明"}]},
        {"options": [{"value": "a", "label": "A", "detail": " "}]},
        {"options": [{"value": "a", "label": "A", "detail": "说明"}] * 2},
    ],
)
def test_invalid_question_is_rejected_before_waiting(patch: dict[str, object]) -> None:
    from forgecli.application.tools.tool import ToolInvocationRequest
    from forgecli.domain.tool.errors import PreparationError

    tool = AskUserTool(PendingHumanPromptService())
    arguments = {"question": "选择阶段", **patch}
    result = tool.prepare(
        ToolInvocationRequest("p", "ask_user", arguments), _NO_CONTEXT
    )
    assert isinstance(result, PreparationError)
