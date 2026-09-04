"""统一人机提示通道的判据 (ADR-0043 决策 3 / 9 / 10).

审批与 ask_user 共用一条待答队列, 但**共用的是等待, 不是语义**. 这里盯三件事:

1. 通道保持哑 —— 它只答得出"这一项是我摆出来的吗", 答不出"这一项意味着什么".
2. 语义映射只在 `ApprovalService` 里发生, 且失效方向朝"没批准".
3. 一次释放同时放开两种提示 —— 这是统一通道换来的那类 bug 的消失.
"""

from __future__ import annotations

import threading
import time

import pytest

from forgecli.application.human_prompt import (
    MAX_QUESTIONS_PER_TURN,
    PendingHumanPromptService,
)
from forgecli.application.security.approval_service import ApprovalService
from forgecli.domain.human_prompt import HumanPrompt, PromptChoice, PromptKind
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.approval import (
    ApprovalBinding,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalView,
)
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.interfaces.runtime.human_prompt import BlockingHumanPromptBroker
from support.fakes import tool_plan


def _approval(
    *,
    approval_id: str = "a1",
    scopes: tuple[ApprovalScope, ...] = (ApprovalScope.ONCE, ApprovalScope.WORKSPACE),
    mandatory: bool = False,
) -> ApprovalRequest:
    plan = tool_plan(raw_command="rm -rf build")
    view = ApprovalView(
        plan=plan,
        action_summary="执行 Shell 命令",
        workspace_roots=("/repo",),
        allowed_scopes=scopes,
    )
    return ApprovalRequest(
        approval_id=approval_id,
        binding=ApprovalBinding(
            plan_hash=plan.plan_hash,
            catalog_snapshot_hash="c",
            execution_profile_hash="e",
            policy_version="1",
            mode=SessionMode.from_value("workspace_write/always"),
            view_hash=view.view_hash,
        ),
        view=view,
        mandatory=mandatory,
    )


def _question(prompt_id: str = "q1") -> HumanPrompt:
    return HumanPrompt(
        prompt_id=prompt_id,
        kind=PromptKind.QUESTION,
        title="用哪个数据库?",
        choices=(PromptChoice("postgres", "Postgres"),),
        free_text=True,
    )


def _ask_in_background(broker: BlockingHumanPromptBroker, prompt: HumanPrompt) -> list:
    out: list = []
    thread = threading.Thread(target=lambda: out.append(broker.ask(prompt)))
    thread.start()
    _wait_until(
        lambda: any(
            item["prompt_id"] == prompt.prompt_id for item in broker.list_pending()
        )
    )
    return [thread, out]


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("等待超时")


# ---- 通道保持哑 ----


def test_channel_only_accepts_choices_the_prompt_offered() -> None:
    """通道答得出"这一项是我摆出来的吗", 答不出"这一项意味着什么"."""
    broker = BlockingHumanPromptBroker()
    prompt = HumanPrompt(
        prompt_id="p1",
        kind=PromptKind.APPROVAL,
        title="确认",
        choices=(PromptChoice("once", "允许这一次"), PromptChoice("deny", "拒绝")),
    )
    thread, out = _ask_in_background(broker, prompt)
    assert broker.resolve("p1", "workspace") is False
    assert broker.resolve("p1", "once") is True
    thread.join(timeout=2)
    assert out[0].choice == "once"


def test_channel_refuses_free_text_on_a_closed_prompt() -> None:
    """静默丢掉的话, 用户以为自己写下的理由被记下来了."""
    broker = BlockingHumanPromptBroker()
    prompt = HumanPrompt(
        prompt_id="p1",
        kind=PromptKind.APPROVAL,
        title="确认",
        choices=(PromptChoice("deny", "拒绝"),),
    )
    thread, _ = _ask_in_background(broker, prompt)
    assert broker.resolve("p1", "deny", "但是只这一次") is False
    assert broker.resolve("p1", "deny") is True
    thread.join(timeout=2)


def test_a_prompt_is_answered_only_once() -> None:
    broker = BlockingHumanPromptBroker()
    thread, _ = _ask_in_background(broker, _question())
    assert broker.resolve("q1", "postgres") is True
    thread.join(timeout=2)
    assert broker.resolve("q1", "postgres") is False


def test_a_prompt_with_no_way_to_answer_is_rejected_at_construction() -> None:
    """没有选项又不收文字的提示会永远挂住调用者."""
    with pytest.raises(ValueError):
        HumanPrompt(prompt_id="p", kind=PromptKind.QUESTION, title="?")


# ---- 一次释放放开两种提示 ----


def test_one_release_frees_both_an_approval_and_a_question() -> None:
    """统一通道最实在的收益: 只剩一条队列, 就没有漏放开另一条的可能."""
    broker = BlockingHumanPromptBroker()
    approval = HumanPrompt(
        prompt_id="a1",
        kind=PromptKind.APPROVAL,
        title="确认",
        choices=(PromptChoice("deny", "拒绝"),),
    )
    approval_thread, approval_out = _ask_in_background(broker, approval)
    question_thread, question_out = _ask_in_background(broker, _question())

    broker.release_pending("用户停止了这一轮")

    approval_thread.join(timeout=2)
    question_thread.join(timeout=2)
    assert approval_out[0].resolved is False
    assert question_out[0].resolved is False
    assert "停止" in approval_out[0].note
    assert broker.list_pending() == ()


def test_closed_channel_answers_immediately_instead_of_hanging() -> None:
    broker = BlockingHumanPromptBroker()
    broker.close()
    answer = broker.ask(_question())
    assert answer.resolved is False
    assert broker.list_pending() == ()


# ---- 每轮提问上限只管提问 ----


def test_question_budget_runs_out_and_resets_next_turn() -> None:
    broker = BlockingHumanPromptBroker()
    for index in range(MAX_QUESTIONS_PER_TURN):
        thread, _ = _ask_in_background(broker, _question(f"q{index}"))
        broker.resolve(f"q{index}", "postgres")
        thread.join(timeout=2)

    refused = broker.ask(_question("q_over"))
    assert refused.resolved is False
    assert str(MAX_QUESTIONS_PER_TURN) in refused.note

    broker.begin_turn()
    thread, out = _ask_in_background(broker, _question("q_next_turn"))
    broker.resolve("q_next_turn", "postgres")
    thread.join(timeout=2)
    assert out[0].resolved is True


def test_the_budget_never_touches_approvals() -> None:
    """审批超额之后唯一安全的处置是当成未批准, 那会把一次正常操作变成失败."""
    broker = BlockingHumanPromptBroker()
    for index in range(MAX_QUESTIONS_PER_TURN):
        thread, _ = _ask_in_background(broker, _question(f"q{index}"))
        broker.resolve(f"q{index}", "postgres")
        thread.join(timeout=2)

    approval = HumanPrompt(
        prompt_id="a1",
        kind=PromptKind.APPROVAL,
        title="确认",
        choices=(PromptChoice("once", "允许这一次"),),
    )
    thread, out = _ask_in_background(broker, approval)
    assert broker.resolve("a1", "once") is True
    thread.join(timeout=2)
    assert out[0].resolved is True


# ---- 语义映射只在适配器里 ----


@pytest.mark.parametrize(
    ("choice", "outcome", "scope"),
    [
        ("once", ApprovalOutcome.APPROVED, ApprovalScope.ONCE),
        ("workspace", ApprovalOutcome.APPROVED, ApprovalScope.WORKSPACE),
        ("deny", ApprovalOutcome.DENIED, ApprovalScope.ONCE),
    ],
)
def test_choices_map_back_to_approval_semantics(
    choice: str, outcome: ApprovalOutcome, scope: ApprovalScope
) -> None:
    broker = BlockingHumanPromptBroker()
    service = ApprovalService(broker)
    request = _approval()
    out: list = []
    thread = threading.Thread(target=lambda: out.append(service.request(request)))
    thread.start()
    _wait_until(lambda: bool(broker.list_pending()))
    assert broker.resolve("a1", choice) is True
    thread.join(timeout=2)
    response = out[0]
    assert response.outcome is outcome
    assert response.scope is scope
    assert request.response_error(response) is None


def test_an_unanswered_prompt_is_pending_never_approved() -> None:
    broker = BlockingHumanPromptBroker()
    service = ApprovalService(broker)
    request = _approval()
    out: list = []
    thread = threading.Thread(target=lambda: out.append(service.request(request)))
    thread.start()
    _wait_until(lambda: bool(broker.list_pending()))
    broker.release_pending("用户停止了这一轮")
    thread.join(timeout=2)
    assert out[0].outcome is ApprovalOutcome.PENDING
    assert out[0].approved is False


def test_no_one_to_answer_is_pending_never_approved() -> None:
    """非交互装配下的安全默认."""
    service = ApprovalService(PendingHumanPromptService())
    response = service.request(_approval())
    assert response.outcome is ApprovalOutcome.PENDING
    assert response.approved is False


def test_the_default_service_is_the_safe_one() -> None:
    """不给通道就是没接界面, 那必须停在 PENDING 而不是放行."""
    assert ApprovalService().request(_approval()).approved is False


def test_a_scope_the_view_forbids_never_reaches_the_model() -> None:
    """两道闸: 通道不认这个选项, 而且就算认了 response_error 也会拦下.

    通道那道是"这一项我没摆出来过", response_error 那道是"这一项不在 allowed_scopes
    里" —— 后者在协调器, 换掉底下的队列它一动不动 (ADR-0043 决策 3).
    """
    broker = BlockingHumanPromptBroker()
    service = ApprovalService(broker)
    request = _approval(scopes=(ApprovalScope.ONCE,))
    out: list = []
    thread = threading.Thread(target=lambda: out.append(service.request(request)))
    thread.start()
    _wait_until(lambda: bool(broker.list_pending()))
    assert broker.resolve("a1", "workspace") is False
    assert broker.resolve("a1", "once") is True
    thread.join(timeout=2)
    assert request.response_error(out[0]) is None


def test_mandatory_ask_offers_only_once() -> None:
    """Mandatory Ask 只能创建与本次请求严格绑定的一次性审批 (ADR-0013 §4.1)."""
    from forgecli.application.security.approval_service import _prompt_of

    prompt = _prompt_of(_approval(scopes=(ApprovalScope.ONCE,), mandatory=True))
    assert [choice.value for choice in prompt.choices] == ["once", "deny"]
    assert prompt.detail["mandatory"] is True
    assert prompt.free_text is False
