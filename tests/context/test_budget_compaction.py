"""预算驱动的两级压缩 (ADR-0032 决策 1 / 2 / 6.1).

一级降级是确定性的, 二级摘要要调模型. 顺序不能颠倒 —— 能用一级解决的绝不用二级,
因为被摘要吃掉的原文再也拼不回来.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from forgecli.application.context.manager import ContextManager
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.context.compaction import CompactionLevel
from forgecli.domain.conversation.message import (
    ChatMessage,
    TextBlock,
    ToolResultBlock,
)
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
from forgecli.domain.tool.result import ResultProvenance
from support.fakes import MemoryArtifactStore

_BIG = "x" * 8000


class _SummaryGateway(LlmGateway):
    """只会写摘要的替身. 记下它收到的请求, 好断言用途标签与"不带工具"."""

    def __init__(self, summary: str = "前面读了几个文件, 都没改动.") -> None:
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
            usage=ModelUsage(input_tokens=10, output_tokens=5),
            latency_ms=1.0,
        )

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        raise NotImplementedError

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        raise NotImplementedError


def _archived(store: MemoryArtifactStore, call_id: str, body: str) -> ChatMessage:
    ref = store.write(invocation_id=call_id, name="output", data=body)
    return ChatMessage(
        role=MessageRole.TOOL,
        content=(
            ToolResultBlock(
                tool_call_id=call_id,
                content=body,
                provenance=ResultProvenance(
                    artifact_id=ref.artifact_id, byte_size=ref.size
                ),
            ),
        ),
    )


def _turn(store: MemoryArtifactStore, index: int) -> tuple[ChatMessage, ...]:
    return (
        ChatMessage(role=MessageRole.USER, content=(TextBlock(f"第 {index} 个问题"),)),
        _archived(store, f"c{index}", _BIG),
    )


@pytest.fixture
def store() -> MemoryArtifactStore:
    return MemoryArtifactStore()


def test_a_roomy_budget_leaves_everything_alone(store: MemoryArtifactStore) -> None:
    messages = tuple(m for i in range(3) for m in _turn(store, i))

    result = ContextManager(artifacts=store).fit(
        messages,
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=1_000_000),
    )

    assert result.drafts == ()
    assert result.messages == messages


def test_a_tight_budget_downgrades_the_oldest_results_first(
    store: MemoryArtifactStore,
) -> None:
    messages = tuple(m for i in range(6) for m in _turn(store, i))

    result = ContextManager(artifacts=store).fit(
        messages,
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=4000),
    )

    assert [draft.level for draft in result.drafts] == [CompactionLevel.DOWNGRADE]
    bodies = [
        block.content
        for message in result.messages
        for block in message.content
        if isinstance(block, ToolResultBlock)
    ]
    assert _BIG not in bodies[0], "最旧的应该被降级"
    assert bodies[-1] == _BIG, "最近的两条留着不动"
    assert "artifact_read" in bodies[0], "占位必须说清怎么取回来"


def test_downgrade_runs_before_summary(store: MemoryArtifactStore) -> None:
    """一级确定性且可逆, 二级不可复现. 顺序反了就是白白丢掉原文.

    窗口取 8000: 降级之后正好落回阈值以内, 于是"有没有调模型"这个断言测的是顺序,
    不是"压不压得下去".
    """
    gateway = _SummaryGateway()
    messages = tuple(m for i in range(6) for m in _turn(store, i))

    result = ContextManager(artifacts=store, gateway=gateway).fit(
        messages,
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=8000),
    )

    assert result.drafts[0].level is CompactionLevel.DOWNGRADE
    assert gateway.requests == [], "一级压下去了就不该动模型"


def test_summary_kicks_in_when_downgrade_is_not_enough(
    store: MemoryArtifactStore,
) -> None:
    gateway = _SummaryGateway()
    # 正文全在 USER 消息里, 没有 artifact 可降 —— 一级无从下手, 只能走二级.
    messages = tuple(
        ChatMessage(role=MessageRole.USER, content=(TextBlock(_BIG),))
        for _ in range(10)
    )

    result = ContextManager(artifacts=store, gateway=gateway).fit(
        messages,
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=4000),
    )

    assert [draft.level for draft in result.drafts] == [CompactionLevel.SUMMARY]
    assert result.drafts[0].summary == gateway.summary
    assert len(result.messages) < len(messages)


def test_the_summary_call_carries_the_compact_origin_and_no_tools(
    store: MemoryArtifactStore,
) -> None:
    """带上工具目录, 模型会开始请求工具而不是写摘要."""
    gateway = _SummaryGateway()
    messages = tuple(
        ChatMessage(role=MessageRole.USER, content=(TextBlock(_BIG),))
        for _ in range(10)
    )

    ContextManager(artifacts=store, gateway=gateway).fit(
        messages,
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=4000),
    )

    request = gateway.requests[0]
    assert request.origin is RequestOrigin.COMPACT
    assert request.tools == ()
    assert request.system_prompt is None


def test_without_a_gateway_it_reports_that_it_still_does_not_fit(
    store: MemoryArtifactStore,
) -> None:
    messages = tuple(
        ChatMessage(role=MessageRole.USER, content=(TextBlock(_BIG),))
        for _ in range(10)
    )

    result = ContextManager(artifacts=store).fit(
        messages,
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=1000),
    )

    assert result.over_allowance is True


def test_a_collected_artifact_says_so_instead_of_promising_a_retrieval(
    store: MemoryArtifactStore,
) -> None:
    """3 天回收之后仍然指着它的引用 —— 让模型去取一个不存在的 id 会让它反复重试."""
    messages = tuple(m for i in range(6) for m in _turn(store, i))
    store.contents.clear()

    result = ContextManager(artifacts=store).fit(
        messages,
        session_id="s1",
        turn_id="t1",
        budget=ContextBudget(context_window=4000),
    )

    bodies = [
        block.content
        for message in result.messages
        for block in message.content
        if isinstance(block, ToolResultBlock)
    ]
    assert "已过期回收" in bodies[0]
    assert "artifact_read" not in bodies[0]


def test_twenty_sixteen_kib_reads_fit_in_the_default_window(
    store: MemoryArtifactStore,
) -> None:
    """ADR-0032 背景第 1 条那个场景.

    fs_read 单次内联上限 16 KiB, 按 4 字符折 1 token 约 4096; 用户没配 context_window
    时目录取的保守默认是 32768. 也就是说读六七个文件就到顶 —— 而这不是长任务才碰得到
    的天花板, 是日常.
    """
    body = "x" * 16 * 1024
    messages: list[ChatMessage] = []
    for index in range(20):
        messages.append(
            ChatMessage(
                role=MessageRole.USER, content=(TextBlock(f"读第 {index} 个文件"),)
            )
        )
        messages.append(_archived(store, f"c{index}", body))
    budget = ContextBudget(context_window=32768)

    result = ContextManager(artifacts=store).fit(
        tuple(messages), session_id="s1", turn_id="t1", budget=budget
    )

    assert result.over_allowance is False
    assert result.estimated_input <= budget.allowance
