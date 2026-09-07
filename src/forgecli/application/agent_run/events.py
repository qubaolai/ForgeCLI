"""AgentRunEvent 的订阅端口与进程内总线 (ADR-0016 §3).

事件词汇住在 domain.agent.run_events; 这里只放"怎么把它分发出去"——编排设施.
本模块取代 ADR-0010 §7.2 的 LoopEventBus, 生产装配不保留两套并行总线 (§决策 1).

三条约束:

- **subscriber 不返回控制信号.** 需要改变循环方向的能力必须实现 LoopHook.
- **subscriber 失败被隔离.** renderer 崩了不能把 Agent 一起带走 (§10.2).
- **sequence 由总线分配.** 发布者只提交 kind 与 payload, 拿回一个已编号的事件.

同步分发, 保持单线程 REPL 下的确定顺序; subscriber 应保持轻量, 耗时操作不要放在
on_event 里 (§3).
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable

from forgecli.domain.agent.run_events import (
    AgentRunEvent,
    AgentRunEventKind,
    RunEventPayload,
)

__all__ = ["AgentRunEventBus", "AgentRunEventSubscriber"]


def _new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


class AgentRunEventSubscriber(ABC):
    """运行事件订阅者. 只消费, 不返回控制信号."""

    @abstractmethod
    def on_event(self, event: AgentRunEvent) -> None:
        """处理一个事件. 抛出的异常会被总线隔离, 不影响 Agent 主循环."""


# (出错的订阅者, 当时那条事件, 异常).
_IsolatedFailure = tuple[AgentRunEventSubscriber, AgentRunEvent, Exception]


class AgentRunEventBus:
    """进程内运行事件总线: 编号, 按注册顺序分发, 隔离 subscriber 异常."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        event_id_factory: Callable[[], str] = _new_event_id,
    ) -> None:
        self._subscribers: list[AgentRunEventSubscriber] = []
        # 按 (会话, 轮次) 计数: sequence 是"这一轮里的第几件事", 跨轮重新从 1 开始
        # (§3). 只按 turn_id 计数的话, 两个会话的 turn_0001 会共用一个计数器 ——
        # 于是新会话的第一条事件带着上一个会话攒下来的序号 (ADR-0048 决策 2).
        # 用单调时钟而不是墙钟算耗时, 系统改时间不会让某一步显示成负数毫秒.
        self._clock = clock
        self._new_event_id = event_id_factory
        self._sequences: dict[tuple[str, str], int] = {}
        self._isolated: list[_IsolatedFailure] = []

    def subscribe(self, subscriber: AgentRunEventSubscriber) -> None:
        self._subscribers.append(subscriber)

    def publish(
        self,
        kind: AgentRunEventKind,
        *,
        session_id: str,
        turn_id: str,
        payload: RunEventPayload,
        step_index: int | None = None,
        request_id: str | None = None,
        tool_call_id: str | None = None,
        invocation_id: str | None = None,
    ) -> AgentRunEvent:
        """编号并分发一个事件, 返回它.

        分发失败不影响返回值: 事件"发生过"这件事与"谁看到了"无关.

        ``session_id`` 由调用方显式给出, 不从日志上下文反推 (ADR-0048 决策 2): 日志
        上下文是给排查用的, 它绑在哪个线程上是可观测性的事, 而事件归属是业务事实 ——
        两者今天恰好一致, 明天多一个线程就不一致了, 而那时不会有任何东西报错.
        """
        key = (session_id, turn_id)
        sequence = self._sequences.get(key, 0) + 1
        self._sequences[key] = sequence
        event = AgentRunEvent(
            event_id=self._new_event_id(),
            kind=kind,
            session_id=session_id,
            turn_id=turn_id,
            sequence=sequence,
            occurred_at=self._clock(),
            payload=payload,
            step_index=step_index,
            request_id=request_id,
            tool_call_id=tool_call_id,
            invocation_id=invocation_id,
        )
        self._dispatch(event)
        return event

    def _dispatch(self, event: AgentRunEvent) -> None:
        for subscriber in self._subscribers:
            try:
                subscriber.on_event(event)
            except Exception as exc:  # noqa: BLE001 - 有意隔离, 见模块 docstring
                self._isolated.append((subscriber, event, exc))
