"""压缩那次模型调用的账 (ADR-0037).

二级摘要是 Forge 自己发起的一次额外模型调用, 它的 input 大致等于被压掉的那段历史 ——
不是零头. 早先 `summarize()` 把 `response.usage` 原地丢掉, 于是这笔钱既不进
`USAGE_RECORDED`, 也不进本轮合计, 用户拿总数去核账单永远对不上.

这组用例钉的就是"它确实被记上了", 以及**记在哪一类**: origin=compact, 让展示层能把
它与用户这句话直接引起的调用分开.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from forgecli.application.context.manager import ContextManager
from forgecli.application.llm.catalog import InMemoryModelCatalog
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.metering import CostEstimator, UsageMeter
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.request import ModelRequest, StructuredModelRequest
from forgecli.domain.model.response import (
    FinishReason,
    ModelResponse,
    ModelUsage,
    StructuredModelResponse,
)
from forgecli.domain.model.streaming import ModelStreamChunk

_BIG = "x" * 8000


class _Gateway(LlmGateway):
    """记下它收到的请求, 并按脚本回一段摘要."""

    def __init__(self, summary: str = "前面读了几个文件.") -> None:
        self.summary = summary
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            request_id=request.request_id,
            provider="fake",
            model="fake-model",
            content=self.summary,
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=4321, output_tokens=120),
            latency_ms=12.0,
        )

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        raise NotImplementedError

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        raise NotImplementedError


def _meter() -> UsageMeter:
    return UsageMeter(CostEstimator(InMemoryModelCatalog()))


def _history(turns: int) -> tuple[ChatMessage, ...]:
    """一段足够长, 且切点落在配对之外的历史."""
    messages: list[ChatMessage] = []
    for index in range(turns):
        messages.append(
            ChatMessage(role=MessageRole.USER, content=(TextBlock(f"问题 {index}"),))
        )
        messages.append(
            ChatMessage(role=MessageRole.ASSISTANT, content=(TextBlock(_BIG),))
        )
    return tuple(messages)


@pytest.fixture
def gateway() -> _Gateway:
    return _Gateway()


def test_the_summary_call_produces_a_usage_draft(gateway: _Gateway) -> None:
    result = ContextManager(gateway=gateway, meter=_meter()).fit(
        _history(8),
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=4000),
    )

    assert len(result.usage_drafts) == 1
    draft = result.usage_drafts[0]
    assert draft.usage.input_tokens == 4321
    assert draft.session_id == "s1"
    assert draft.turn_id == "t1"


def test_the_draft_is_tagged_as_compaction(gateway: _Gateway) -> None:
    """展示层靠 origin 把这一笔与用户这句话引起的调用分开."""
    result = ContextManager(gateway=gateway, meter=_meter()).fit(
        _history(8),
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=4000),
    )

    assert result.usage_drafts[0].origin is RequestOrigin.COMPACT


def test_an_empty_summary_still_costs_money() -> None:
    """请求已经发出去了, 摘要好不好用与这笔钱花没花无关.

    早先这条路径直接 return, 把这种情况下的用量整个吞掉 —— 而"模型回了一段空白"恰恰
    是最该被看见的一种浪费.
    """
    result = ContextManager(gateway=_Gateway(summary="  "), meter=_meter()).fit(
        _history(8),
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=4000),
    )

    assert result.drafts == (), "空摘要顶不掉任何东西, 所以没有压缩记录"
    assert len(result.usage_drafts) == 1, "但这次调用确实发生过, 必须记账"


def test_without_a_meter_compaction_still_works(gateway: _Gateway) -> None:
    """缺一个可选协作件不该让功能塌掉, 只是这次不产出草稿."""
    result = ContextManager(gateway=gateway).fit(
        _history(8),
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=4000),
    )

    assert result.usage_drafts == ()
    assert any(draft.level.value == "summary" for draft in result.drafts)


def test_manual_compaction_also_meters(gateway: _Gateway) -> None:
    """`/compact` 不经 AgentLoop, 但同样要记账."""
    result = ContextManager(gateway=gateway, meter=_meter()).summarize_now(
        _history(8), session_id="s1", turn_id="t1"
    )

    assert len(result.usage_drafts) == 1
    assert result.usage_drafts[0].origin is RequestOrigin.COMPACT
