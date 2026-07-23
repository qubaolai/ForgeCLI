"""扩展机制之一：LoopHook（ADR-0010 §7.1）。

hooks 可影响控制流：按稳定顺序运行，返回显式 HookResult（继续 / 改状态 / 改请求 /
停）。与只读的 LoopEvent（§7.2）分层——需要改变循环方向的能力必须实现 hook，而不是
event subscriber。hook 不直接写事件：需要记录时发布 LoopEvent 或交由 AgentTurnService
写入。

约束：hook 可以要求 stop，但必须给出 LoopStopReason。

今日（2026-07-23）只冻端口与 HookResult；hook 默认实现为「放行」，让具体 hook 只覆写
关心的时点（BudgetHook / ModePolicyHook / ApprovalHook / RetryHook 等属后续切片）。
"""

from __future__ import annotations
from dataclasses import dataclass

from forgecli.application.agent_loop.actions import LoopAction, LoopObservation
from forgecli.application.agent_loop.state import LoopState
from forgecli.application.agent_loop.stop import LoopStopReason
from forgecli.application.llm.gateway.request import ModelRequest
from forgecli.application.llm.gateway.response import ModelResponse


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
    

class LoopHook:
    """按稳定顺序影响控制流的 hook。默认各时点放行，子类只覆写关心的时点。"""

    def before_step(self, state: LoopState) -> HookResult:
        return HookResult.proceed()

    def before_model(self, request: ModelRequest, state: LoopState) -> HookResult:
        return HookResult.proceed()

    def after_model(self, response: ModelResponse, state: LoopState) -> HookResult:
        return HookResult.proceed()

    def before_action(self, action: LoopAction, state: LoopState) -> HookResult:
        return HookResult.proceed()

    def after_action(
        self, observation: LoopObservation, state: LoopState
    ) -> HookResult:
        return HookResult.proceed()