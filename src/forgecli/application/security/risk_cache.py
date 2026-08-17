"""脚本风险分析的内容哈希缓存 (ADR-0013 §12).

存在的理由很实际: 一个 turn 里可能跑五次同一个测试脚本, 每次都问一遍 LLM 既慢又贵.

但缓存**不是安全边界**. 两条约束写死在用法里:

- 键包含内容, 依赖, 命令, 工作目录, 策略版本, 执行画像, 解析器与分析器版本和意图范围
  (``domain.security.risk.risk_cache_key`` 负责组装). 少一项就会在本该失效时命中.
- 命中缓存仍然要重新跑 Hard Deny 与当前策略检查 —— 缓存省掉的只是分类器那一次调用.

只缓存**可用**结论: 把一次超时缓存下来, 等于让接下来每次都超时.
"""

from __future__ import annotations

from forgecli.domain.security.risk import RiskReport

__all__ = ["RiskCache"]

_DEFAULT_CAPACITY = 256


class RiskCache:
    """进程内的 LRU 缓存.

    不落盘: 跨进程复用要求把执行画像一并持久化比对, 收益不抵复杂度.
    """

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._entries: dict[str, RiskReport] = {}

    def get(self, key: str) -> RiskReport | None:
        report = self._entries.pop(key, None)
        if report is not None:
            self._entries[key] = report  # 命中即最近使用
        return report

    def put(self, key: str, report: RiskReport) -> None:
        if not report.usable:
            return
        self._entries.pop(key, None)
        self._entries[key] = report
        while len(self._entries) > self._capacity:
            oldest = next(iter(self._entries))
            del self._entries[oldest]

    def invalidate(self, key: str) -> bool:
        return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
