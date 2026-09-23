"""用量计量与费用估算服务（ADR-0011 §11）。

值对象（UnitPrices / UsageRecordDraft）住在 domain.model.usage；这里只留两个服务：
CostEstimator 持有目录算钱，UsageMeter 持有估算器与时钟产出草稿。
"""

from __future__ import annotations

from collections.abc import Callable

from forgecli.common.clock import now_iso
from forgecli.core.model.model_ref import ModelRef
from forgecli.core.model.request import ModelRequest
from forgecli.core.model.response import ModelResponse, ModelUsage
from forgecli.core.model.usage import UnitPrices, UsageRecordDraft
from forgecli.llm.catalog import ModelCatalogService


class CostEstimator:
    """按目录价格元数据估算一次调用成本（§11.2）。只读，不写盘。"""

    def __init__(self, catalog: ModelCatalogService) -> None:
        self._catalog = catalog

    def unit_prices(self, ref: ModelRef) -> UnitPrices:
        """模型的单价快照；模型不在目录或价格未声明时对应项为 None。"""
        if not self._catalog.has_model(ref):
            return UnitPrices()
        entry = self._catalog.get(ref)
        return UnitPrices(
            input_per_1k=entry.input_price_per_1k,
            output_per_1k=entry.output_price_per_1k,
            cached_input_per_1k=entry.cached_input_price_per_1k,
            reasoning_per_1k=entry.reasoning_price_per_1k,
        )

    def estimate_cost(self, prices: UnitPrices, usage: ModelUsage) -> float | None:
        """估算成本; input/output 任一单价缺失 -> None (价格未知, 不阻塞调用).

        口径 (§11.2 / ADR-0012 §7) 跟 `ModelUsage` 的定义走: cached 与 reasoning 都是
        子集, 先从 input / output 里减出来, 再各按各的单价算; 专价缺失时退化为所属
        那一项的单价. 早先 reasoning 分支写成 `output + reasoning`, 而供应商回的
        `completion_tokens` 本来就含 reasoning, 于是没配 reasoning 单价时那部分算了两次.
        """
        if prices.input_per_1k is None or prices.output_per_1k is None:
            return None
        cached = min(usage.cached_input_tokens, usage.input_tokens)
        fresh_input = usage.input_tokens - cached
        cached_price = (
            prices.cached_input_per_1k
            if prices.cached_input_per_1k is not None
            else prices.input_per_1k
        )
        reasoning = min(usage.reasoning_tokens, usage.output_tokens)
        plain_output = usage.output_tokens - reasoning
        reasoning_price = (
            prices.reasoning_per_1k
            if prices.reasoning_per_1k is not None
            else prices.output_per_1k
        )
        return (
            fresh_input * prices.input_per_1k
            + cached * cached_price
            + plain_output * prices.output_per_1k
            + reasoning * reasoning_price
        ) / 1000.0


class UsageMeter:
    """把 ModelRequest + ModelResponse 转成计量草稿（§11.1）。不落盘。"""

    def __init__(
        self,
        cost_estimator: CostEstimator,
        *,
        clock: Callable[[], str] = now_iso,
    ) -> None:
        self._cost = cost_estimator
        self._clock = clock

    def build_draft(
        self, request: ModelRequest, response: ModelResponse
    ) -> UsageRecordDraft:
        ref = ModelRef(provider=response.provider, model=response.model)
        prices = self._cost.unit_prices(ref)
        # 响应缓存命中（§14）：真实 usage 记 0，因此成本自然为 0。
        cost = self._cost.estimate_cost(prices, response.usage)
        return UsageRecordDraft(
            request_id=response.request_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            turn_id=request.turn_id,
            provider=response.provider,
            model=response.model,
            origin=request.origin,
            usage=response.usage,
            estimated=response.usage.estimated,
            unit_prices=prices,
            estimated_cost=cost,
            latency_ms=response.latency_ms,
            created_at=self._clock(),
        )
