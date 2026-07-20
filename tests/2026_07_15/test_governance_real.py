"""治理实裁决（ADR-0012 §8）：健康熔断 / 预算。全部离线确定性。

不含客户端主动限流（RateLimiter）：对单用户单 key 的个人 CLI 而言，本地按配置
猜测的限流阈值没有信息优势，交互 origin 上的快速失败只会拒绝掉 provider 本可能
接受的请求；429 已由短等重试环（ADR-0012 §2）与凭证冷却处理，该能力已移除。
"""

from __future__ import annotations

import pytest

from forgecli.application.llm.gateway import (
    BudgetSnapshot,
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelBudgetExceededError,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ModelTimeoutError,
    ModelUnavailableError,
    ProviderRegistry,
    RequestOrigin,
    SlidingWindowHealthRegistry,
    SnapshotBudgetGuard,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _registry(clock: _Clock) -> SlidingWindowHealthRegistry:
    return SlidingWindowHealthRegistry(
        failure_threshold=3, cooldown_seconds=30.0, clock=clock
    )


def test_circuit_opens_after_consecutive_failures() -> None:
    clock = _Clock()
    registry = _registry(clock)
    for _ in range(3):
        registry.record_failure(_REF)
    with pytest.raises(ModelUnavailableError) as excinfo:
        registry.check(_REF)
    assert "熔断" in str(excinfo.value)


def test_success_resets_failure_streak() -> None:
    clock = _Clock()
    registry = _registry(clock)
    registry.record_failure(_REF)
    registry.record_failure(_REF)
    registry.record_success(_REF)  # 连续性被打断
    registry.record_failure(_REF)
    registry.check(_REF)  # 未达阈值，不熔断


def test_half_open_allows_single_probe_then_recovers() -> None:
    clock = _Clock()
    registry = _registry(clock)
    for _ in range(3):
        registry.record_failure(_REF)
    clock.now = 31.0  # 冷却到期
    registry.check(_REF)  # half-open：放行 1 个探测
    with pytest.raises(ModelUnavailableError):
        registry.check(_REF)  # 探测在途，其余仍拒绝
    registry.record_success(_REF)  # 探测成功 -> closed
    registry.check(_REF)


def test_half_open_probe_failure_reopens() -> None:
    clock = _Clock()
    registry = _registry(clock)
    for _ in range(3):
        registry.record_failure(_REF)
    clock.now = 31.0
    registry.check(_REF)  # 放行探测
    registry.record_failure(_REF)  # 探测失败 -> 继续 open
    clock.now = 32.0
    with pytest.raises(ModelUnavailableError):
        registry.check(_REF)


def test_budget_guard_rejects_over_limit_without_provider_call() -> None:
    guard = SnapshotBudgetGuard()
    with pytest.raises(ModelBudgetExceededError):
        guard.check(
            BudgetSnapshot(session_tokens_used=990, session_tokens_limit=1000),
            estimated_input_tokens=20,
        )
    with pytest.raises(ModelBudgetExceededError):
        guard.check(
            BudgetSnapshot(turn_tokens_used=990, turn_tokens_limit=1000),
            estimated_input_tokens=20,
        )


def test_budget_guard_passes_within_limit_and_without_snapshot() -> None:
    guard = SnapshotBudgetGuard()
    guard.check(
        BudgetSnapshot(session_tokens_used=0, session_tokens_limit=10_000),
        estimated_input_tokens=100,
    )
    guard.check(None, estimated_input_tokens=999_999)  # 快照缺失直通


def _request() -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )


def test_gateway_circuit_opens_and_blocks_provider_calls() -> None:
    """集成：连续 timeout 触发熔断后，后续调用不再打到 provider。"""
    clock = _Clock()
    provider = FakeModelProvider(error=ModelTimeoutError("timeout"))
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek", model="deepseek-chat", context_window=65536
            ),
        )
    )
    gw = DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        health_registry=SlidingWindowHealthRegistry(
            failure_threshold=3, cooldown_seconds=30.0, clock=clock
        ),
    )
    with pytest.raises(ModelTimeoutError):
        gw.complete(_request())  # 默认 max_retries=2 -> 3 次失败达到阈值
    calls_before = provider.complete_calls
    with pytest.raises(ModelUnavailableError):
        gw.complete(_request())  # 熔断中：请求前拒绝
    assert provider.complete_calls == calls_before  # 未再打 provider
