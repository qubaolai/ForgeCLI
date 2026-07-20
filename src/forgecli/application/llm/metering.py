"""usage / cost 计量草稿（ADR-0011 §11）。

`UsageRecordDraft` 是待落盘的计量草稿：字段与最终 UsageRecord 一致，但尚未分配
事件顺序、也不表示已持久化。**写入边界属于 AgentTurnService**（§11.1）：gateway
不写事件 / usage 文件；`UsageMeter` 在 AgentTurnService 一侧把 ModelRequest +
ModelResponse 转成草稿，由 AgentTurnService 决定写 session event、独立 usage
文件或未来 sqlite。

`CostEstimator`（§11.2）读模型目录的价格元数据估算成本：价格缺失时
estimated_cost=None，不阻塞模型调用，由 /status 或 usage 汇总标记价格未知。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from forgecli.application.llm.gateway.catalog import ModelCatalogService
from forgecli.application.llm.gateway.origin import RequestOrigin
from forgecli.application.llm.gateway.request import ModelRequest
from forgecli.application.llm.gateway.response import ModelResponse, ModelUsage
from forgecli.application.llm.model_ref import ModelRef


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class UnitPrices:
    """单价快照（每 1k token；None = 未知）。随草稿留档，便于追溯计费口径。"""

    input_per_1k: float | None = None
    output_per_1k: float | None = None
    cached_input_per_1k: float | None = None
    reasoning_per_1k: float | None = None


@dataclass(frozen=True)
class UsageRecordDraft:
    """一次模型调用的计量草稿（§11.1）。落盘与事件顺序由 AgentTurnService 负责。"""

    request_id: str
    session_id: str
    turn_id: str
    provider: str
    model: str
    origin: RequestOrigin
    usage: ModelUsage
    estimated: bool
    unit_prices: UnitPrices
    estimated_cost: float | None
    latency_ms: float
    created_at: str

    def to_payload(self) -> dict[str, object]:
        """转成可 JSON 落盘的事件 payload（只含安全摘要，无凭证 / 原文）。"""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "provider": self.provider,
            "model": self.model,
            "origin": self.origin.value,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cached_input_tokens": self.usage.cached_input_tokens,
            "reasoning_tokens": self.usage.reasoning_tokens,
            "total_tokens": self.usage.total_tokens,
            "estimated": self.estimated,
            "unit_price_input_per_1k": self.unit_prices.input_per_1k,
            "unit_price_output_per_1k": self.unit_prices.output_per_1k,
            "estimated_cost": self.estimated_cost,
            "latency_ms": self.latency_ms,
            "created_at": self.created_at,
        }


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
        """估算成本；input/output 任一单价缺失 -> None（价格未知，不阻塞调用）。

        口径（§11.2 / ADR-0012 §7）：cached input 按 cached 单价（缺失退化为
        input 单价）；reasoning token 有专价用专价，缺失并入 output 计价
        （多数供应商口径）。
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
        if prices.reasoning_per_1k is not None:
            reasoning_cost = usage.reasoning_tokens * prices.reasoning_per_1k
            output_tokens = usage.output_tokens
        else:
            reasoning_cost = 0.0
            output_tokens = usage.output_tokens + usage.reasoning_tokens
        return (
            fresh_input * prices.input_per_1k
            + cached * cached_price
            + output_tokens * prices.output_per_1k
            + reasoning_cost
        ) / 1000.0


class UsageMeter:
    """把 ModelRequest + ModelResponse 转成计量草稿（§11.1）。不落盘。"""

    def __init__(
        self,
        cost_estimator: CostEstimator,
        *,
        clock: Callable[[], str] = _now_iso,
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
