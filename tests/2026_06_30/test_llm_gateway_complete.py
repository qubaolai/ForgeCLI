"""DefaultLlmGateway.complete：happy path / 估算 usage / 错误归一 / 确定 latency。

（2026-06-30，随 ADR-0012 §10 迁移）全部离线、不读 env、不打网络；latency 由
注入 timer 钉死。resolver 已成为必备协作件：本文件改为注入
DefaultModelSelectionResolver，断言语义不变。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    ExplicitModelSelection,
    FinishReason,
    InMemoryModelCatalog,
    ModelBadRequestError,
    ModelCatalogEntry,
    ModelParams,
    ModelProviderInternalError,
    ModelRateLimitError,
    ModelRequest,
    ModelResponse,
    ModelSelection,
    ModelUsage,
    ProviderRegistry,
    RequestOrigin,
    TextBlock,
)
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider


def _timer_seq(*values: float) -> Callable[[], float]:
    values_iter = iter(values)
    return lambda: next(values_iter)


def _request(selection: ModelSelection | None = None) -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=selection
        or ExplicitModelSelection(provider="deepseek", model="deepseek-chat"),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )


def _gateway(
    provider: FakeModelProvider, timer: Callable[[], float] | None = None
) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek", model="deepseek-chat", context_window=65536
            ),
        )
    )
    # 未配置当前模型（current_model=None）：explicit 正常解析，
    # current selection 报错——语义与 06-30 兼容路径一致。
    resolver = DefaultModelSelectionResolver(catalog, current_model=None)
    if timer is None:
        return DefaultLlmGateway(registry, resolver=resolver)
    return DefaultLlmGateway(registry, resolver=resolver, timer=timer)


def test_happy_path_returns_normalized_response() -> None:
    usage = ModelUsage(input_tokens=4, output_tokens=6, total_tokens=10)
    gw = _gateway(FakeModelProvider(content="hi there", usage=usage))
    resp = gw.complete(_request())
    # 只暴露归一 DTO，不把 provider 私有响应对象漏给上层。
    assert isinstance(resp, ModelResponse)
    assert resp.request_id == "req_1"
    assert resp.provider == "deepseek"
    assert resp.model == "deepseek-chat"
    assert resp.content == "hi there"
    assert resp.finish_reason is FinishReason.STOP
    assert resp.usage is usage
    assert resp.usage.estimated is False


def test_missing_usage_is_estimated() -> None:
    gw = _gateway(FakeModelProvider(content="hi"))  # provider 不给 usage
    resp = gw.complete(_request())
    assert resp.usage.estimated is True


def test_provider_gateway_error_propagates_as_is() -> None:
    gw = _gateway(FakeModelProvider(error=ModelRateLimitError("429")))
    with pytest.raises(ModelRateLimitError):
        gw.complete(_request())


def test_non_gateway_error_is_normalized() -> None:
    gw = _gateway(FakeModelProvider(error=RuntimeError("boom")))
    with pytest.raises(ModelProviderInternalError) as exc_info:
        gw.complete(_request())
    assert exc_info.value.provider == "deepseek"
    assert exc_info.value.request_id == "req_1"


def test_latency_is_deterministic_with_injected_timer() -> None:
    gw = _gateway(FakeModelProvider(content="x"), timer=_timer_seq(1.0, 1.25))
    resp = gw.complete(_request())
    assert resp.latency_ms == 250.0


def test_current_selection_without_configured_model_rejected() -> None:
    # 迁移自「MVP 拒绝 current selection」：resolver 注入后语义等价——
    # 未配置当前模型时 current selection 仍然是 bad request。
    gw = _gateway(FakeModelProvider())
    with pytest.raises(ModelBadRequestError):
        gw.complete(_request(CurrentModelSelection()))
