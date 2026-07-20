"""gateway 的窗口预检与真实 / 估算 usage 归一化（ADR-0011 §3.8 / §11.4）。

覆盖：context window 超限在 provider 调用前拦截；provider 给 usage 用真实值；
缺 usage 时按 TokenEstimator 估算（非零）并标 estimated=True。
"""

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelCatalogEntry,
    ModelContextOverflowError,
    ModelParams,
    ModelRequest,
    ModelUsage,
    ProviderRegistry,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


def _gateway(
    provider: FakeModelProvider, *, context_window: int = 65536
) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek",
                model="deepseek-chat",
                context_window=context_window,
            ),
        )
    )
    resolver = DefaultModelSelectionResolver(catalog, current_model=_REF)
    return DefaultLlmGateway(registry, resolver=resolver)


def _request(text: str = "hi") -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock(text),)),),
        params=ModelParams(),
    )


def test_context_overflow_intercepted_before_provider_call() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, context_window=10)
    with pytest.raises(ModelContextOverflowError):
        gw.complete(_request("x" * 4000))
    assert provider.complete_calls == 0  # 未发起 provider 调用


def test_within_window_passes() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, context_window=65536)
    assert gw.complete(_request()).content == "ok"


def test_real_usage_used_as_is() -> None:
    usage = ModelUsage(input_tokens=7, output_tokens=3, total_tokens=10)
    gw = _gateway(FakeModelProvider(content="ok", usage=usage))
    resp = gw.complete(_request())
    assert resp.usage is usage
    assert resp.usage.estimated is False


def test_missing_usage_estimated_with_nonzero_tokens() -> None:
    gw = _gateway(FakeModelProvider(content="a fairly long answer body text"))
    resp = gw.complete(_request("请解释一下什么是统一网关" * 4))
    assert resp.usage.estimated is True
    assert resp.usage.input_tokens > 0
    assert resp.usage.output_tokens > 0
    assert resp.usage.total_tokens == (
        resp.usage.input_tokens + resp.usage.output_tokens
    )
