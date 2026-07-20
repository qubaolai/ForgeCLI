"""治理件按 §8 顺序织入调用链（ADR-0011 §11.3 / §12.1 / §17）。

MVP 为 no-op，但端口已在管线上：注入记录型 / 拒绝型实现即可生效，
后续真实裁决不需要改 gateway 主路径。不含客户端主动限流（曾属 §13）：
个人单 key CLI 场景下已移除，见 gateway/governance.py 模块说明。
"""

import pytest

from forgecli.application.llm.gateway import (
    BudgetGuard,
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
    ModelUnavailableError,
    NoopBudgetGuard,
    NoopLlmCacheController,
    NoopProviderHealthRegistry,
    ProviderHealthRegistry,
    ProviderRegistry,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


class _OpenCircuitHealth(ProviderHealthRegistry):
    def check(self, ref: ModelRef) -> None:
        raise ModelUnavailableError(
            f"模型 {ref} 熔断中", provider=ref.provider, model=ref.model
        )

    def record_success(self, ref: ModelRef) -> None:  # pragma: no cover
        return None

    def record_failure(self, ref: ModelRef) -> None:  # pragma: no cover
        return None


class _HardBudgetGuard(BudgetGuard):
    def check(
        self,
        snapshot: BudgetSnapshot | None,
        *,
        estimated_input_tokens: int,
    ) -> None:
        if snapshot is None:
            return
        if snapshot.session_tokens_limit is not None and (
            snapshot.session_tokens_used + estimated_input_tokens
            > snapshot.session_tokens_limit
        ):
            raise ModelBudgetExceededError("session 预算超限")


def _gateway(provider: FakeModelProvider, **kwargs: object) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek", model="deepseek-chat", context_window=65536
            ),
        )
    )
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        **kwargs,  # type: ignore[arg-type]
    )


def _request(budget: BudgetSnapshot | None = None) -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
        budget_snapshot=budget,
    )


def test_noop_governance_does_not_change_call_chain() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(
        provider,
        health_registry=NoopProviderHealthRegistry(),
        budget_guard=NoopBudgetGuard(),
        cache=NoopLlmCacheController(),
    )
    assert gw.complete(_request()).content == "ok"


def test_open_circuit_health_blocks_before_provider_call() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, health_registry=_OpenCircuitHealth())
    with pytest.raises(ModelUnavailableError):
        gw.complete(_request())
    assert provider.complete_calls == 0


def test_budget_guard_rejects_over_budget_before_provider_call() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, budget_guard=_HardBudgetGuard())
    over_budget = BudgetSnapshot(session_tokens_used=999, session_tokens_limit=1000)
    with pytest.raises(ModelBudgetExceededError):
        gw.complete(_request(budget=over_budget))
    assert provider.complete_calls == 0


def test_budget_guard_passes_within_budget() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, budget_guard=_HardBudgetGuard())
    within = BudgetSnapshot(session_tokens_used=0, session_tokens_limit=10_000)
    assert gw.complete(_request(budget=within)).content == "ok"
