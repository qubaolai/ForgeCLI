"""网关治理：provider/model 健康熔断（ADR-0011 §12.1，ADR-0012 §8）。

跨请求的健康与熔断状态；只存在于进程内运行时，不落盘、不跨 session。连续失败达阈值
open，冷却到期 half-open 放行 1 个探测请求。

**未启用时由这一个类自己直通**（ADR-0028 规则 A）。早先是"端口 + Noop + 真实实现"
三件套，由 wiring 按配置二选一装配——三个类表达的其实是一个布尔量，而端口只有这两个
实现，其中一个什么都不做。

BudgetGuard 已整体删除（ADR-0028 规则 C）：它比对的 `ModelRequest.budget_snapshot`
全库没有任何生产方，于是 `SnapshotBudgetGuard.check` 每次都走"快照缺失直通"。一个
永远不生效的校验点，比没有校验点更糟——它让人以为预算已经接线了。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from forgecli.application.llm.gateway.errors import ModelUnavailableError
from forgecli.domain.model.model_ref import ModelRef


class _CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _Circuit:
    state: _CircuitState = _CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0


class SlidingWindowHealthRegistry:
    """滑动窗口熔断（ADR-0012 §8）：连续失败达阈值 -> open（冷却）；
    冷却到期 -> half-open 放行 1 个探测请求；成功 -> closed，失败 -> 继续 open。
    状态仅进程内，不落盘、不跨 session。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold 必须为正整数")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds 必须为正数")
        # 未启用时三个方法都直通. 由这个类自己表达, 不再单独一个 Noop 实现:
        # [llm.circuit_breaker] 的开关是一个布尔量, 不该膨胀成一个类层级.
        self._enabled = enabled
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._circuits: dict[tuple[str, str], _Circuit] = {}

    def check(self, ref: ModelRef) -> None:
        if not self._enabled:
            return
        circuit = self._circuit(ref)
        if circuit.state is _CircuitState.CLOSED:
            return
        if circuit.state is _CircuitState.OPEN:
            remaining = circuit.opened_at + self._cooldown - self._clock()
            if remaining > 0:
                raise ModelUnavailableError(
                    f"模型 {ref} 熔断中（连续失败达 {self._threshold} 次），"
                    f"约 {remaining:.0f}s 后进入半开探测",
                    provider=ref.provider,
                    model=ref.model,
                )
            circuit.state = _CircuitState.HALF_OPEN  # 冷却到期: 放行本次 改为半开
            return
        # HALF_OPEN：探测请求已在途，其余调用继续拒绝。
        raise ModelUnavailableError(
            f"模型 {ref} 熔断半开，探测请求进行中",
            provider=ref.provider,
            model=ref.model,
        )

    def record_success(self, ref: ModelRef) -> None:
        if not self._enabled:
            return
        circuit = self._circuit(ref)
        circuit.state = _CircuitState.CLOSED
        circuit.consecutive_failures = 0

    def record_failure(self, ref: ModelRef) -> None:
        if not self._enabled:
            return
        circuit = self._circuit(ref)
        if circuit.state is _CircuitState.HALF_OPEN:
            circuit.state = _CircuitState.OPEN  # 探测失败：重新进入冷却
            circuit.opened_at = self._clock()
            return
        circuit.consecutive_failures += 1
        if circuit.consecutive_failures >= self._threshold:
            circuit.state = _CircuitState.OPEN
            circuit.opened_at = self._clock()

    def _circuit(self, ref: ModelRef) -> _Circuit:
        return self._circuits.setdefault((ref.provider, ref.model), _Circuit())
