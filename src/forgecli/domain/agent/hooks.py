"""hook 的返回值词汇（ADR-0010 §7.1）。

HookResult 描述的是"一次扩展点回调可以对循环提出什么要求": 放行 / 停止 / 换状态 /
换请求。这四种可能性是循环契约的一部分, 与谁来实现 hook 无关, 故属领域。

LoopHook 这个端口本身留在 application: 它是给编排层挂扩展的插槽。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.agent.state import LoopState
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.model.request import ModelRequest

__all__ = ["HookResult"]


@dataclass(frozen=True)
class HookResult:
    """hook 的显式返回值（§7.1）。

    should_continue=False 时必须带 stop_reason；mutated_state / mutated_request 非空表示
    hook 请求以新值替换后续状态 / 模型请求（必须可测试、可追踪）。
    """

    should_continue: bool = True
    stop_reason: LoopStopReason | None = None
    mutated_state: LoopState | None = None
    mutated_request: ModelRequest | None = None

    def __post_init__(self) -> None:
        if not self.should_continue and self.stop_reason is None:
            raise ValueError("HookResult 要求 stop 时必须给出 stop_reason")

    @classmethod
    def proceed(cls) -> HookResult:
        """放行，不改变控制流。"""
        return cls()

    @classmethod
    def stop(cls, reason: LoopStopReason) -> HookResult:
        """要求循环停止，并给出原因。"""
        return cls(should_continue=False, stop_reason=reason)

    @classmethod
    def mutate(
        cls,
        *,
        state: LoopState | None = None,
        request: ModelRequest | None = None,
    ) -> HookResult:
        """放行但请求替换 state / request。"""
        return cls(mutated_state=state, mutated_request=request)
