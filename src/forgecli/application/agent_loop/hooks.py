"""扩展机制之一：LoopHook 端口（ADR-0010 §7.1）。

hooks 可**影响控制流**：按稳定顺序运行，返回显式 HookResult（继续 / 改状态 / 改请求 /
停）。与只读的 LoopEvent（§7.2）分层——需要改变循环方向的能力必须实现 hook，而不是
event subscriber。hook 不直接写事件：需要记录时发布 LoopEvent 或交由 AgentTurnService
写入。

返回值 HookResult 住在 domain.agent.hooks —— 它是循环契约的一部分。
"""

from __future__ import annotations

from forgecli.domain.agent.actions import LoopAction, LoopObservation
from forgecli.domain.agent.hooks import HookResult
from forgecli.domain.agent.state import LoopState
from forgecli.domain.model.request import ModelRequest
from forgecli.domain.model.response import ModelResponse

__all__ = ["LoopHook"]


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
