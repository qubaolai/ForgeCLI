"""网关可观测性产出（ADR-0012 §9，ADR-0011 §15 落地第一步）。

网关在每次调用收尾（complete 返回、stream 收尾 chunk、错误抛出）统一产出
`GatewayCallSample` 安全摘要样本：只含 provider / model / origin / finish /
latency / 重试计数 / cache 命中 / 错误分类，**不含凭证与 prompt/response 原文**。

`InProcessGatewayMetrics` 是默认装配的进程内聚合实现（纯内存、无 IO、无副作用，
故不设开关）：按 provider/model 聚合调用数、错误分类计数、延迟分桶与 cache
命中数，`snapshot()` 产出只读视图供 /status 消费（展示接入属调用方切片）。
trace span 导出、审计落盘形态留后续；样本字段本 ADR 先冻结。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from forgecli.application.llm.gateway.origin import RequestOrigin
from forgecli.application.llm.gateway.response import FinishReason

# 延迟分桶上界（ms）；最后追加一个「以上」桶。
_LATENCY_BUCKET_UPPER_MS: tuple[float, ...] = (100.0, 500.0, 2000.0, 10000.0)


@dataclass(frozen=True)
class GatewayCallSample:
    """一次网关调用的安全摘要样本（字段冻结，ADR-0012 §9）。"""

    provider: str
    model: str
    origin: RequestOrigin
    finish_reason: FinishReason | None
    latency_ms: float
    credential_retries: int = 0
    transport_retries: int = 0
    wait_retries: int = 0
    cache_hit: bool = False
    estimated: bool = False
    error_type: str | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("GatewayCallSample 必须带 provider 与 model")
        if self.latency_ms < 0:
            raise ValueError("GatewayCallSample.latency_ms 不能为负")


class GatewayObserver(ABC):
    """调用收尾统一上报端口。实现不得落盘、不得阻塞调用路径。"""

    @abstractmethod
    def on_call(self, sample: GatewayCallSample) -> None:
        """complete 返回 / stream 收尾 / 错误抛出三个位置统一上报。"""


@dataclass
class _ModelMetrics:
    calls: int = 0
    cache_hits: int = 0
    estimated_usage_calls: int = 0
    credential_retries: int = 0
    transport_retries: int = 0
    wait_retries: int = 0
    errors: dict[str, int] | None = None
    latency_buckets: list[int] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = {}
        if self.latency_buckets is None:
            self.latency_buckets = [0] * (len(_LATENCY_BUCKET_UPPER_MS) + 1)


class InProcessGatewayMetrics(GatewayObserver):
    """进程内聚合：按 provider/model 汇总样本。纯内存，不写任何文件。"""

    def __init__(self) -> None:
        self._metrics: dict[str, _ModelMetrics] = {}

    def on_call(self, sample: GatewayCallSample) -> None:
        metrics = self._metrics.setdefault(
            f"{sample.provider}/{sample.model}", _ModelMetrics()
        )
        metrics.calls += 1
        if sample.cache_hit:
            metrics.cache_hits += 1
        if sample.estimated:
            metrics.estimated_usage_calls += 1
        metrics.credential_retries += sample.credential_retries
        metrics.transport_retries += sample.transport_retries
        metrics.wait_retries += sample.wait_retries
        if sample.error_type is not None:
            assert metrics.errors is not None
            metrics.errors[sample.error_type] = (
                metrics.errors.get(sample.error_type, 0) + 1
            )
        assert metrics.latency_buckets is not None
        metrics.latency_buckets[_bucket_index(sample.latency_ms)] += 1

    def snapshot(self) -> dict[str, dict[str, object]]:
        """只读视图（深拷贝）：供 /status 展示与未来 trace 导出消费。"""
        view: dict[str, dict[str, object]] = {}
        for key, metrics in self._metrics.items():
            assert metrics.errors is not None
            assert metrics.latency_buckets is not None
            view[key] = {
                "calls": metrics.calls,
                "cache_hits": metrics.cache_hits,
                "estimated_usage_calls": metrics.estimated_usage_calls,
                "credential_retries": metrics.credential_retries,
                "transport_retries": metrics.transport_retries,
                "wait_retries": metrics.wait_retries,
                "errors": dict(metrics.errors),
                "latency_buckets": {
                    label: count
                    for label, count in zip(
                        _bucket_labels(), metrics.latency_buckets, strict=True
                    )
                },
            }
        return view


class NoopGatewayObserver(GatewayObserver):
    """空实现：测试或显式关闭聚合时使用。"""

    def on_call(self, sample: GatewayCallSample) -> None:
        return None


def _bucket_index(latency_ms: float) -> int:
    for index, upper in enumerate(_LATENCY_BUCKET_UPPER_MS):
        if latency_ms <= upper:
            return index
    return len(_LATENCY_BUCKET_UPPER_MS)


def _bucket_labels() -> tuple[str, ...]:
    labels = [f"<={int(upper)}ms" for upper in _LATENCY_BUCKET_UPPER_MS]
    labels.append(f">{int(_LATENCY_BUCKET_UPPER_MS[-1])}ms")
    return tuple(labels)
