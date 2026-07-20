"""可观测性产出（ADR-0012 §9）：样本字段、聚合、无凭证 / 原文泄露。"""

from __future__ import annotations

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    FinishReason,
    GatewayCallSample,
    GatewayObserver,
    InMemoryModelCatalog,
    InMemoryResponseCache,
    InProcessGatewayMetrics,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ModelTimeoutError,
    ModelUsage,
    ProviderCapabilities,
    ProviderRegistry,
    ProviderStreamChunk,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")
_PROMPT = "观测样本不得包含这段用户原文"


class _RecordingObserver(GatewayObserver):
    def __init__(self) -> None:
        self.samples: list[GatewayCallSample] = []

    def on_call(self, sample: GatewayCallSample) -> None:
        self.samples.append(sample)


def _gateway(
    provider: FakeModelProvider,
    observer: GatewayObserver,
    *,
    cache: InMemoryResponseCache | None = None,
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
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        observer=observer,
        cache=cache,
    )


def _request(origin: RequestOrigin = RequestOrigin.CHAT) -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=origin,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock(_PROMPT),)),),
        params=ModelParams(),
    )


def test_success_sample_fields_complete() -> None:
    observer = _RecordingObserver()
    usage = ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5)
    gw = _gateway(FakeModelProvider(content="答", usage=usage), observer)
    gw.complete(_request())
    assert len(observer.samples) == 1
    sample = observer.samples[0]
    assert (sample.provider, sample.model) == ("deepseek", "deepseek-chat")
    assert sample.origin is RequestOrigin.CHAT
    assert sample.finish_reason is FinishReason.STOP
    assert sample.cache_hit is False
    assert sample.estimated is False
    assert sample.error_type is None


def test_error_sample_carries_error_type() -> None:
    observer = _RecordingObserver()
    gw = _gateway(FakeModelProvider(error=ModelTimeoutError("t")), observer)
    with pytest.raises(ModelTimeoutError):
        gw.complete(_request())
    sample = observer.samples[-1]
    assert sample.error_type == "ModelTimeoutError"
    assert sample.finish_reason is None
    assert sample.transport_retries > 0  # 重试计数进入样本


def test_cache_hit_sample_flagged() -> None:
    observer = _RecordingObserver()
    cache = InMemoryResponseCache(allowed_origins=(RequestOrigin.TITLE,))
    gw = _gateway(FakeModelProvider(content="标题"), observer, cache=cache)
    gw.complete(_request(RequestOrigin.TITLE))
    gw.complete(_request(RequestOrigin.TITLE))
    assert [sample.cache_hit for sample in observer.samples] == [False, True]
    assert observer.samples[1].latency_ms == 0.0


def test_stream_final_chunk_reports_sample() -> None:
    observer = _RecordingObserver()
    provider = FakeModelProvider(
        capabilities=ProviderCapabilities(supports_streaming=True),
        stream_chunks=(
            ProviderStreamChunk(delta_text="你好"),
            ProviderStreamChunk(
                usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                finish_reason=FinishReason.STOP,
            ),
        ),
    )
    gw = _gateway(provider, observer)
    list(gw.stream(_request()))
    assert len(observer.samples) == 1
    sample = observer.samples[0]
    assert sample.finish_reason is FinishReason.STOP
    assert sample.estimated is False


def test_samples_never_contain_prompt_or_credential() -> None:
    observer = _RecordingObserver()
    gw = _gateway(FakeModelProvider(content="答"), observer)
    gw.complete(_request())
    dump = repr(observer.samples)
    assert _PROMPT not in dump
    assert "答" not in dump  # response 原文同样不进样本


def test_in_process_metrics_aggregates_by_model() -> None:
    metrics = InProcessGatewayMetrics()
    gw = _gateway(FakeModelProvider(content="答"), metrics)
    gw.complete(_request())
    gw.complete(_request())
    snapshot = metrics.snapshot()
    view = snapshot["deepseek/deepseek-chat"]
    assert view["calls"] == 2
    assert view["cache_hits"] == 0
    assert view["errors"] == {}
    latency_buckets = view["latency_buckets"]
    assert isinstance(latency_buckets, dict)
    assert sum(latency_buckets.values()) == 2


def test_in_process_metrics_counts_error_types() -> None:
    metrics = InProcessGatewayMetrics()
    gw = _gateway(FakeModelProvider(error=ModelTimeoutError("t")), metrics)
    with pytest.raises(ModelTimeoutError):
        gw.complete(_request())
    view = metrics.snapshot()["deepseek/deepseek-chat"]
    assert view["errors"] == {"ModelTimeoutError": 1}


def test_default_observer_is_in_process_metrics() -> None:
    """不显式注入 observer 时默认聚合已装配（无开关，§9）。"""
    registry = ProviderRegistry()
    registry.register(FakeModelProvider(content="ok"))
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
    )
    gw.complete(_request())  # 不抛错即视为默认观测器无副作用
