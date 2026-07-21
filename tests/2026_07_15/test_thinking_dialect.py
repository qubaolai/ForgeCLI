"""thinking 方言翻译（ADR-0012 §4）：effort 型 / budget 型 / none 型。

用 httpx.MockTransport 捕获请求 payload，全部离线。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from forgecli.application.llm.gateway.errors import ModelBadRequestError
from forgecli.application.llm.gateway.messages import ChatMessage, TextBlock
from forgecli.application.llm.gateway.params import (
    ModelParams,
    ThinkingConfig,
)
from forgecli.application.llm.gateway.provider import ProviderRequest
from forgecli.application.llm.providers import ThinkingDialect
from forgecli.application.llm.thinking import ThinkingEffortName
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import OpenAICompatibleProvider

_COMPLETION = {
    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
}


def _provider(
    dialect: ThinkingDialect, captured: list[dict[str, Any]]
) -> OpenAICompatibleProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_COMPLETION)

    return OpenAICompatibleProvider(
        provider_id="deepseek",
        base_url="https://fake.invalid",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        thinking_dialect=dialect,
    )


def _request(
    thinking: ThinkingConfig | None,
    *,
    model_max_output_tokens: int | None = None,
) -> ProviderRequest:
    return ProviderRequest(
        model="deepseek-chat",
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
        thinking=thinking,
        model_max_output_tokens=model_max_output_tokens,
    )


def _payload(
    dialect: ThinkingDialect,
    thinking: ThinkingConfig | None,
    *,
    model_max_output_tokens: int | None = None,
) -> dict[str, Any]:
    captured: list[dict[str, Any]] = []
    provider = _provider(dialect, captured)
    provider.complete(
        _request(thinking, model_max_output_tokens=model_max_output_tokens)
    )
    return captured[0]


def test_effort_dialect_sends_reasoning_effort() -> None:
    payload = _payload(
        ThinkingDialect.EFFORT,
        ThinkingConfig(enabled=True, effort=ThinkingEffortName("medium")),
    )
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "medium"
    assert "thinking_budget" not in payload


def test_budget_dialect_maps_effort_to_budget_tokens() -> None:
    payload = _payload(
        ThinkingDialect.BUDGET,
        ThinkingConfig(enabled=True, effort=ThinkingEffortName("medium")),
    )
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["thinking_budget"] == 4096  # 档位表 medium -> 4096
    assert "reasoning_effort" not in payload


def test_budget_mapping_truncated_by_model_max_output_tokens() -> None:
    payload = _payload(
        ThinkingDialect.BUDGET,
        ThinkingConfig(enabled=True, effort=ThinkingEffortName("high")),
        model_max_output_tokens=2048,
    )
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["thinking_budget"] == 2048  # high=16384 受 max_output 截断


def test_budget_mapping_not_truncated_without_catalog_hint() -> None:
    payload = _payload(
        ThinkingDialect.BUDGET,
        ThinkingConfig(enabled=True, effort=ThinkingEffortName("high")),
        model_max_output_tokens=None,
    )
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["thinking_budget"] == 16384  # catalog 缺失时不截断


def test_budget_dialect_rejects_unmapped_open_effort() -> None:
    with pytest.raises(ModelBadRequestError, match="无法映射强度"):
        _payload(
            ThinkingDialect.BUDGET,
            ThinkingConfig(enabled=True, effort=ThinkingEffortName("max")),
        )


def test_none_dialect_sends_no_thinking_fields() -> None:
    payload = _payload(
        ThinkingDialect.NONE,
        ThinkingConfig(enabled=True, effort=ThinkingEffortName("high")),
    )
    assert "reasoning_effort" not in payload
    assert "thinking_budget" not in payload
    assert "thinking" not in payload


def test_thinking_off_sends_disabled_switch_without_effort() -> None:
    payload = _payload(
        ThinkingDialect.BUDGET,
        ThinkingConfig(enabled=False, effort=ThinkingEffortName("high")),
    )
    assert payload["thinking"] == {"type": "disabled"}
    assert "thinking_budget" not in payload


def test_thinking_enabled_without_effort_still_sends_switch() -> None:
    payload = _payload(
        ThinkingDialect.EFFORT,
        ThinkingConfig(enabled=True),
    )
    assert payload["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in payload
