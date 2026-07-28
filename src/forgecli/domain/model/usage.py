"""用量与费用的词汇（ADR-0011 §11）。

UnitPrices 是四档单价, UsageRecordDraft 是一次调用的安全摘要（含 to_payload()，
它只做字段重排不碰 IO）。两者都不随计费实现变化, 故属领域。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.response import ModelUsage


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
