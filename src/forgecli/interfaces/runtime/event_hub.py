"""把进程内 AgentRunEvent 存成可重连的有限缓冲。

SSE 靠它补发断线期间的事件, 终端入口靠它在刷新处理过程时重建时间线 —— 两边看的是
同一个缓冲, 所以它不该叫 Web 什么 (ADR-0045)。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field

from forgecli.application.agent_run.events import AgentRunEventSubscriber
from forgecli.domain.agent.run_events import AgentRunEvent
from forgecli.shared.serialization import to_jsonable


@dataclass(frozen=True)
class WebEvent:
    cursor: int
    event_id: str
    kind: str
    # 这条事件属于哪个会话 (ADR-0048 决策 2). 缓冲跨会话共用, 所以每条都要自报归属 ——
    # 否则切过会话之后, 谁都分不出缓冲里哪一段是当前会话的.
    session_id: str
    data: dict[str, object]


@dataclass(frozen=True)
class ResumePoint:
    """续传位置: 哪一个 hub 实例的第几条 (ADR-0048 决策 2).

    光有游标不够. 游标是进程内递增的整数, 每个新 hub 都从 0 起 —— 切项目或重启之后,
    客户端手里那个"100"在新实例上指的是一段还没发生的未来, 而旧写法会安静地等它.
    """

    stream_id: str
    cursor: int

    def token(self) -> str:
        return f"{self.stream_id}:{self.cursor}"

    @staticmethod
    def parse(raw: str | None) -> ResumePoint | None:
        """解析续传标识. 认不出来就返回 None, 由调用方按"从头开始"处理.

        旧版纯数字游标也落在这里: 它证明不了实例一致, 所以不当作有效续传位置
        (ADR-0048 影响一节).
        """
        if not raw:
            return None
        stream_id, separator, cursor = raw.partition(":")
        if not separator or not stream_id:
            return None
        try:
            return ResumePoint(stream_id=stream_id, cursor=int(cursor))
        except ValueError:
            return None


@dataclass(eq=False)
class _StreamWaiter:
    """一条 SSE 流的等待句柄；事件由 Agent 线程产生，唤醒必须回到它自己的循环。"""

    loop: asyncio.AbstractEventLoop
    flag: asyncio.Event = field(default_factory=asyncio.Event)

    def wake(self) -> None:
        # 循环已关闭时 call_soon_threadsafe 会抛 RuntimeError；那条流本来就不再读了。
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.flag.set)


class RunEventHub(AgentRunEventSubscriber):
    """线程安全的有限事件缓冲；慢客户端过期后必须重新同步会话状态。"""

    def __init__(self, *, max_events: int = 2048) -> None:
        self._events: deque[WebEvent] = deque(maxlen=max_events)
        # 每个实例一个身份. 客户端带回来的续传位置只有配上它才有意义.
        self._stream_id = f"stream_{uuid.uuid4().hex[:12]}"
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
                    # 事件身份带上会话: 两个会话都有 turn_0001, 只按 turn 与 sequence
                    # 拼出来的 id 会在它们之间撞车 (ADR-0048 决策 2).
                    event_id=f"{event.session_id}:{event.turn_id}:{event.sequence}",
                    kind=event.kind.value,
                    session_id=event.session_id,
                    data=envelope,
                )
            )
            waiters = tuple(self._waiters)
        for waiter in waiters:
            waiter.wake()

    def after(self, resume: ResumePoint | None) -> tuple[bool, tuple[WebEvent, ...]]:
        """返回 ``(是否需要全量同步, 续传位置之后的事件)``。

        三种情况都要求重新同步, 一视同仁 (ADR-0048 决策 2):

        - **实例不匹配**: 客户端手里是另一个 hub 的位置。切项目与重启进程都会走到这里。
        - **游标超前**: 位置比这个实例发出过的还大。旧写法在这里返回"没有新事件",
          于是客户端一直等到新实例的计数追上那个旧数字 —— 中间的事件全都被跳过, 而
          页面上什么异常都看不出来。
        - **缓冲过期**: 要的那一段已经被挤出去了。

        取事件时从右往左取到第一条已发送的为止：缓冲区按 cursor 递增，全量扫描会让每条
        增量都付一次整个缓冲区的代价，正是流式输出最密集的时候。
        """
        with self._lock:
            if resume is None or resume.stream_id != self._stream_id:
                return (True, ())
            cursor = resume.cursor
            if cursor > self._cursor:
                return (True, ())
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

    def buffered(self, session_id: str = "") -> tuple[WebEvent, ...]:
        """进程内仍保留的事件；页面刷新后据此重建各 turn 的处理过程。

        给了 ``session_id`` 就只回那个会话的。缓冲跨会话共用, 不过滤就会把上一个会话的
        处理过程混进当前时间线 —— 而两个会话都有 ``turn_0001``, 混进去之后按 turn 分组
        还会把它们叠在一起 (ADR-0048 决策 2)。
        """
        with self._lock:
            events = tuple(self._events)
        if not session_id:
            return events
        return tuple(item for item in events if item.session_id == session_id)

    def resume_point(self) -> ResumePoint:
        """当前水位。快照带上它, 客户端才知道该从哪一条接着往下应用。"""
        with self._lock:
            return ResumePoint(stream_id=self._stream_id, cursor=self._cursor)

    @property
    def stream_id(self) -> str:
        return self._stream_id

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
