"""进程内计数与耗时聚合 (ADR-0035 决策 5).

`Metrics()` 默认**关闭** —— 采集由配置项 `telemetry.enabled` 控制, 默认是 false.
所以这些用例都显式打开它: 它们问的是 "开着的时候聚合对不对", 关掉之后的行为由
文件末尾那三条单独钉.
"""

from __future__ import annotations

from forgecli.shared.observability.metrics import Metrics


def test_labels_are_sorted_into_a_single_key() -> None:
    """同一组标签只能有一个键, 否则同一件事会被分散到两行读数上."""
    metrics = Metrics(enabled=True)
    metrics.count("llm.call", provider="openai", model="gpt-4o")
    metrics.count("llm.call", model="gpt-4o", provider="openai")

    assert metrics.snapshot()["counters"] == {
        "llm.call{model=gpt-4o,provider=openai}": 2
    }


def test_durations_report_percentiles() -> None:
    metrics = Metrics(enabled=True)
    for value in (10.0, 20.0, 30.0, 40.0, 100.0):
        metrics.observe("tool.execute", value)

    view = metrics.snapshot()["durations"]["tool.execute"]
    assert view == {
        "count": 5,
        "total_ms": 200.0,
        "avg_ms": 40.0,
        "min_ms": 10.0,
        "p50_ms": 30.0,
        "p95_ms": 100.0,
        "max_ms": 100.0,
    }


def test_snapshot_is_a_copy() -> None:
    """展示层拿到的读数不该随后续调用变化, 否则一次 dump 里前后两段对不上."""
    metrics = Metrics(enabled=True)
    metrics.count("tool.denied")
    taken = metrics.snapshot()
    metrics.count("tool.denied")

    assert taken["counters"] == {"tool.denied": 1}


def test_reset_clears_everything() -> None:
    metrics = Metrics(enabled=True)
    metrics.count("a")
    metrics.observe("b", 1.0)
    metrics.reset()

    assert metrics.snapshot() == {"counters": {}, "durations": {}}


def test_collection_is_off_by_default() -> None:
    """默认关: 没走过启动装配的路径 (测试, 脚本) 不该静默攒数据."""
    metrics = Metrics()
    metrics.count("a")
    metrics.observe("b", 1.0)

    assert metrics.enabled is False
    assert metrics.snapshot() == {"counters": {}, "durations": {}}


def test_turning_it_off_clears_what_was_collected() -> None:
    """关掉之后 /diagnostics 还显示着半截数据, 用户会以为开关没生效."""
    metrics = Metrics(enabled=True)
    metrics.count("a")
    metrics.set_enabled(False)

    assert metrics.snapshot() == {"counters": {}, "durations": {}}


def test_turning_it_back_on_resumes_collection() -> None:
    metrics = Metrics(enabled=True)
    metrics.set_enabled(False)
    metrics.set_enabled(True)
    metrics.count("a")

    assert metrics.snapshot()["counters"] == {"a": 1}
