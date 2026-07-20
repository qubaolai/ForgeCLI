"""网关治理：健康熔断 / 预算裁决（ADR-0011 §12.1 / §13，ADR-0012 §8）。

端口自 MVP 起已织入调用链（§8 顺序），默认 no-op 不改变调用行为。ADR-0012 §8
补齐真实实现，**配置驱动启用**：配置未开启时 wiring 维持 no-op 装配，行为渐进
切换，不破坏既有调用链。

    - ProviderHealthRegistry（§12.1）：跨请求的 provider/model 健康与熔断状态；
      只存在于进程内运行时，不落盘、不跨 session。真实实现
      SlidingWindowHealthRegistry：连续失败达阈值 open，冷却到期 half-open 放行
      1 个探测请求。
    - BudgetGuard（§11.3）：请求前只读预算校验点，基于 AgentTurnService 注入的
      预算快照裁决；超限抛 ModelBudgetExceededError，不发起 provider 调用。
      真实实现 SnapshotBudgetGuard 做快照比对；快照缺失直通（预算规则与已用量
      状态仍归 AgentTurnService）。

"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from forgecli.application.llm.gateway.errors import (
    ModelBudgetExceededError,
    ModelUnavailableError,
)
from forgecli.application.llm.gateway.request import BudgetSnapshot
from forgecli.application.llm.model_ref import ModelRef


class ProviderHealthRegistry(ABC):
    """provider/model 健康与熔断端口（§12.1）。状态仅进程内，不落盘。"""

    @abstractmethod
    def check(self, ref: ModelRef) -> None:
        """选中该模型时检查熔断状态；熔断中应抛 ModelUnavailableError。"""

    @abstractmethod
    def record_success(self, ref: ModelRef) -> None:
        """记录一次成功（半开状态下恢复）。"""

    @abstractmethod
    def record_failure(self, ref: ModelRef) -> None:
        """记录一次连接失败 / 5xx / 超时（累计到阈值进入熔断）。"""


class NoopProviderHealthRegistry(ProviderHealthRegistry):
    """直通实现：永远健康，不熔断（配置未启用时的默认装配）。"""

    def check(self, ref: ModelRef) -> None:
        return None

    def record_success(self, ref: ModelRef) -> None:
        return None

    def record_failure(self, ref: ModelRef) -> None:
        return None


class _CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _Circuit:
    state: _CircuitState = _CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0


class SlidingWindowHealthRegistry(ProviderHealthRegistry):
    """滑动窗口熔断（ADR-0012 §8）：连续失败达阈值 -> open（冷却）；
    冷却到期 -> half-open 放行 1 个探测请求；成功 -> closed，失败 -> 继续 open。
    状态仅进程内，不落盘、不跨 session。
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold 必须为正整数")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds 必须为正数")
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._circuits: dict[tuple[str, str], _Circuit] = {}

    def check(self, ref: ModelRef) -> None:
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
        circuit = self._circuit(ref)
        circuit.state = _CircuitState.CLOSED
        circuit.consecutive_failures = 0

    def record_failure(self, ref: ModelRef) -> None:
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


class BudgetGuard(ABC):
    """请求前只读预算校验端口（§11.3）。"""

    @abstractmethod
    def check(
        self,
        snapshot: BudgetSnapshot | None,
        *,
        estimated_input_tokens: int,
    ) -> None:
        """比对估算用量与预算快照；超限抛 ModelBudgetExceededError，不发起调用。"""


class NoopBudgetGuard(BudgetGuard):
    """直通实现：不做预算裁决。"""

    def check(
        self,
        snapshot: BudgetSnapshot | None,
        *,
        estimated_input_tokens: int,
    ) -> None:
        return None


class SnapshotBudgetGuard(BudgetGuard):
    """快照比对预算裁决（ADR-0012 §8）：estimated_input + 已用量 > 上限时拒发。

    快照缺失直通——预算规则与已用量状态仍归 AgentTurnService（BudgetPolicy /
    BudgetTracker），本实现只做只读比对，不持有也不更新预算状态。
    """

    def check(
        self,
        snapshot: BudgetSnapshot | None,
        *,
        estimated_input_tokens: int,
    ) -> None:
        if snapshot is None:
            return
        self._check_scope(
            "turn",
            snapshot.turn_tokens_used,
            snapshot.turn_tokens_limit,
            estimated_input_tokens,
        )
        self._check_scope(
            "session",
            snapshot.session_tokens_used,
            snapshot.session_tokens_limit,
            estimated_input_tokens,
        )

    @staticmethod
    def _check_scope(
        scope: str, used: int, limit: int | None, estimated_input: int
    ) -> None:
        if limit is None:
            return
        if used + estimated_input > limit:
            raise ModelBudgetExceededError(
                f"{scope} 预算超限：已用 {used} + 估算输入 {estimated_input} "
                f"超出上限 {limit}，请求未发起"
            )
