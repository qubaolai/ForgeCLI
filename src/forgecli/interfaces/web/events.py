"""把进程内 AgentRunEvent 转成可重连的 Web 事件缓冲。"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections import deque
from dataclasses import dataclass, field

from forgecli.application.agent_run.events import AgentRunEventSubscriber
from forgecli.domain.agent.run_events import AgentRunEvent
from forgecli.interfaces.web.serialization import to_jsonable


@dataclass(frozen=True)
class WebEvent:
    cursor: int
    event_id: str
    kind: str
    data: dict[str, object]


@dataclass(eq=False)
class _StreamWaiter:
    """一条 SSE 流的等待句柄；事件由 Agent 线程产生，唤醒必须回到它自己的循环。"""

    loop: asyncio.AbstractEventLoop
    flag: asyncio.Event = field(default_factory=asyncio.Event)

    def wake(self) -> None:
        # 循环已关闭时 call_soon_threadsafe 会抛 RuntimeError；那条流本来就不再读了。
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.flag.set)


class WebEventHub(AgentRunEventSubscriber):
    """线程安全的有限事件缓冲；慢客户端过期后必须重新同步会话状态。"""

    def __init__(self, *, max_events: int = 2048) -> None:
        self._events: deque[WebEvent] = deque(maxlen=max_events)
        self._cursor = 0
        self._lock = threading.Lock()
        self._waiters: set[_StreamWaiter] = set()
        self._closed = False

    def on_event(self, event: AgentRunEvent) -> None:
        envelope = to_jsonable(event)
        assert isinstance(envelope, dict)
        with self._lock:
            self._cursor += 1
            self._events.append(
                WebEvent(
                    cursor=self._cursor,
                    event_id=f"{event.turn_id}:{event.sequence}",
                    kind=event.kind.value,
                    data=envelope,
                )
            )
            waiters = tuple(self._waiters)
        for waiter in waiters:
            waiter.wake()

    def after(self, cursor: int) -> tuple[bool, tuple[WebEvent, ...]]:
        """返回 ``(是否需要全量同步, cursor 之后的事件)``。

        从右往左取到第一条已发送的为止：缓冲区按 cursor 递增，全量扫描会让每条增量都
        付一次整个缓冲区的代价，正是流式输出最密集的时候。
        """
        with self._lock:
            if self._events and cursor < self._events[0].cursor - 1:
                return (True, ())
            pending: list[WebEvent] = []
            for item in reversed(self._events):
                if item.cursor <= cursor:
                    break
                pending.append(item)
        pending.reverse()
        return (False, tuple(pending))

    async def wait(
        self,
        cursor: int,
        *,
        timeout: float = 15.0,
        stop: asyncio.Event | None = None,
    ) -> None:
        """等待新事件、``stop`` 或超时。

        必须是协程：阻塞式等待会让 SSE 处理任务无法响应服务停止，Uvicorn 的优雅退出
        也就只能一直等下去。
        """
        if self._closed or (stop is not None and stop.is_set()):
            return
        waiter = _StreamWaiter(asyncio.get_running_loop())
        with self._lock:
            if self._closed or self._cursor > cursor:
                return
            self._waiters.add(waiter)
        try:
            await _first_of(waiter.flag, stop, timeout=timeout)
        finally:
            with self._lock:
                self._waiters.discard(waiter)

    def close(self) -> None:
        """停止接收新事件并唤醒全部等待中的流。"""
        with self._lock:
            self._closed = True
            waiters, self._waiters = tuple(self._waiters), set()
        for waiter in waiters:
            waiter.wake()

    def buffered(self) -> tuple[WebEvent, ...]:
        """进程内仍保留的全部事件；页面刷新后据此重建各 turn 的处理过程。"""
        with self._lock:
            return tuple(self._events)

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._cursor

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed


async def _first_of(
    flag: asyncio.Event, stop: asyncio.Event | None, *, timeout: float
) -> None:
    """等到任一 Event 被置位或超时；三种结局都只是返回。"""
    pending = [asyncio.ensure_future(flag.wait())]
    if stop is not None:
        pending.append(asyncio.ensure_future(stop.wait()))
    try:
        await asyncio.wait(
            pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in pending:
            task.cancel()
