"""进程内计数与耗时聚合: 回答"慢在哪, 哪一步在反复失败".

日志回答"这一次发生了什么", 指标回答"这一类调用一共发生了多少次, 花了多久". 排查
单次 bug 靠前者, 找优化点靠后者 —— 一次 turn 走了 40 秒, 日志能让你逐行读出来,
但"40 秒里 31 秒在等 provider, 6 秒在跑 shell_run"要靠聚合才看得见.

刻意做成**纯内存, 进程内, 无采样**: Forge 是本机单进程 CLI, 不存在需要抽样的量级,
也没有可以推送指标的服务端. 想看就现场 dump (`/diagnostics`), 进程退出即丢弃.

`InProcessGatewayMetrics` (ADR-0012 §9) 是网关自己那份按 provider/model 的专用聚合,
与这里并存: 那份的字段是冻结的调用样本, 这份是任意阶段都能记的通用计数. 两份都进
`/diagnostics`, 不互相替代.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

__all__ = ["METRICS", "Metrics"]

# 每个耗时序列最多留多少个样本. 只用来算分位数, 超出后丢最旧的.
#
# 1024 是刻意的小数字: 它不是为了统计严谨, 是为了让一次会话里"最近这些调用有多慢"
# 看得见. 留全量会让一次长任务的 shell_run 攒出几十万个 float.
_SAMPLE_LIMIT = 1024


def _key(name: str, labels: dict[str, str]) -> str:
    """`llm.call{provider=openai,model=gpt-4o}`. 标签排序, 保证同一组标签只有一个键."""
    if not labels:
        return name
    inner = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return f"{name}{{{inner}}}"


@dataclass
class _Durations:
    """一个耗时序列的累计量与最近样本."""

    count: int = 0
    total_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    samples: list[float] = field(default_factory=list)

    def add(self, elapsed_ms: float) -> None:
        self.min_ms = elapsed_ms if self.count == 0 else min(self.min_ms, elapsed_ms)
        self.max_ms = max(self.max_ms, elapsed_ms)
        self.count += 1
        self.total_ms += elapsed_ms
        self.samples.append(elapsed_ms)
        if len(self.samples) > _SAMPLE_LIMIT:
            del self.samples[0]

    def view(self) -> dict[str, float | int]:
        ordered = sorted(self.samples)
        return {
            "count": self.count,
            "total_ms": round(self.total_ms, 3),
            "avg_ms": round(self.total_ms / self.count, 3) if self.count else 0.0,
            "min_ms": round(self.min_ms, 3),
            "p50_ms": _percentile(ordered, 0.50),
            "p95_ms": _percentile(ordered, 0.95),
            "max_ms": round(self.max_ms, 3),
        }


def _percentile(ordered: list[float], ratio: float) -> float:
    """最近样本的分位数. 空序列返回 0.0, 不返回 None —— 展示层不必为它分叉."""
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(round(ratio * (len(ordered) - 1))))
    return round(ordered[index], 3)


class Metrics:
    """按 `名字{标签}` 聚合的计数器与耗时序列. 加锁, 可跨线程写."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._durations: dict[str, _Durations] = {}

    def count(self, name: str, amount: int = 1, **labels: str) -> None:
        """给一个计数器加数. 例: `count("tool.denied", tool="shell_run")`."""
        key = _key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def observe(self, name: str, elapsed_ms: float, **labels: str) -> None:
        """记一次耗时 (毫秒). 由 `Log.span` 自动调用, 也可以手动记."""
        key = _key(name, labels)
        with self._lock:
            self._durations.setdefault(key, _Durations()).add(elapsed_ms)

    def snapshot(self) -> dict[str, dict[str, object]]:
        """只读视图 (深拷贝), 供 `/diagnostics` 与 Web 诊断接口消费."""
        with self._lock:
            counters: dict[str, object] = dict(sorted(self._counters.items()))
            durations: dict[str, object] = {
                key: value.view() for key, value in sorted(self._durations.items())
            }
        return {"counters": counters, "durations": durations}

    def reset(self) -> None:
        """清空. 只给测试与"从现在开始重新量一次"用."""
        with self._lock:
            self._counters.clear()
            self._durations.clear()


# 进程级单例. 指标是进程内的现场读数, 让每个调用点自己拿一个实例传下去只会导致
# 一半的调用记在没人读的那个实例上.
METRICS = Metrics()
