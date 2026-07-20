"""CostEstimator / UsageMeter / UsageRecordDraft（ADR-0011 §11，2026-07-06 切片）。"""

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    InMemoryModelCatalog,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.gateway.response import FinishReason
from forgecli.application.llm.metering import CostEstimator, UnitPrices, UsageMeter
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


def _catalog(**price_fields: float) -> InMemoryModelCatalog:
    return InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek",
                model="deepseek-chat",
                context_window=65536,
                **price_fields,  # type: ignore[arg-type]
            ),
        )
    )


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


def _response(usage: ModelUsage) -> ModelResponse:
    return ModelResponse(
        request_id="req_1",
        provider="deepseek",
        model="deepseek-chat",
        content="ok",
        finish_reason=FinishReason.STOP,
        usage=usage,
        latency_ms=120.0,
    )


def test_cost_computed_from_catalog_prices() -> None:
    estimator = CostEstimator(_catalog(input_price_per_1k=0.2, output_price_per_1k=0.4))
    prices = estimator.unit_prices(_REF)
    usage = ModelUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
    assert estimator.estimate_cost(prices, usage) == 0.2 + 0.2  # 0.2 输入 + 0.2 输出


def test_missing_price_yields_none_cost_without_blocking() -> None:
    estimator = CostEstimator(_catalog())
    prices = estimator.unit_prices(_REF)
    usage = ModelUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
    assert estimator.estimate_cost(prices, usage) is None


def test_unknown_model_prices_are_unknown() -> None:
    estimator = CostEstimator(_catalog())
    prices = estimator.unit_prices(ModelRef(provider="openai", model="nope"))
    assert prices == UnitPrices()


def test_cached_tokens_billed_at_cached_price_or_input_price() -> None:
    estimator = CostEstimator(
        _catalog(
            input_price_per_1k=1.0,
            output_price_per_1k=0.0001,
            cached_input_price_per_1k=0.1,
        )
    )
    prices = estimator.unit_prices(_REF)
    usage = ModelUsage(
        input_tokens=1000, output_tokens=0, cached_input_tokens=500, total_tokens=1000
    )
    # 500 鲜活 * 1.0 + 500 缓存 * 0.1 = 0.55
    cost = estimator.estimate_cost(prices, usage)
    assert cost is not None
    assert abs(cost - 0.55) < 1e-9


def test_reasoning_tokens_billed_as_output() -> None:
    estimator = CostEstimator(_catalog(input_price_per_1k=0.0, output_price_per_1k=1.0))
    prices = UnitPrices(input_per_1k=0.0, output_per_1k=1.0)
    usage = ModelUsage(
        input_tokens=0, output_tokens=100, reasoning_tokens=900, total_tokens=1000
    )
    assert estimator.estimate_cost(prices, usage) == 1.0


def test_usage_meter_builds_complete_draft() -> None:
    meter = UsageMeter(
        CostEstimator(_catalog(input_price_per_1k=0.2, output_price_per_1k=0.4)),
        clock=lambda: "2026-07-06T10:00:00+08:00",
    )
    usage = ModelUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
    draft = meter.build_draft(_request(), _response(usage))
    assert draft.request_id == "req_1"
    assert draft.session_id == "sess_1"
    assert draft.turn_id == "turn_0001"
    assert draft.provider == "deepseek"
    assert draft.model == "deepseek-chat"
    assert draft.origin is RequestOrigin.CHAT
    assert draft.estimated is False
    assert draft.estimated_cost == 0.4
    assert draft.latency_ms == 120.0
    assert draft.created_at == "2026-07-06T10:00:00+08:00"


def test_draft_payload_is_json_safe_summary() -> None:
    meter = UsageMeter(CostEstimator(_catalog()), clock=lambda: "t0")
    usage = ModelUsage(
        input_tokens=10, output_tokens=5, total_tokens=15, estimated=True
    )
    payload = meter.build_draft(_request(), _response(usage)).to_payload()
    assert payload["estimated"] is True
    assert payload["estimated_cost"] is None
    assert payload["origin"] == "chat"
    assert payload["input_tokens"] == 10
