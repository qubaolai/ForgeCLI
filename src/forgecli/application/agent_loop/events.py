"""扩展机制之二：LoopEvent 的订阅端口与最小 LoopEventBus（ADR-0010 §7.2）。

事件本身的词汇（LoopEventKind / LoopEvent）住在 domain.agent.events；这里只放
"怎么把它分发出去"——订阅端口与一个进程内总线，都是编排设施。

约束（§7.2）：
    - subscriber 不返回控制信号。
    - subscriber 失败不能破坏主循环——被隔离并记录，不向上抛。

今日（2026-07-23）落地最小 bus（订阅 + 隔离式发布）；具体 subscriber
（EventLog / UsageMeter / Trace / CliRender）属后续切片。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.agent.events import LoopEvent

__all__ = ["LoopEventBus", "LoopEventSubscriber"]


class LoopEventSubscriber(ABC):
    """循环事件订阅者。只消费，不返回控制信号。"""

    @abstractmethod
    def on_event(self, event: LoopEvent) -> None:
        """处理一个事件。抛出的异常会被 LoopEventBus 隔离，不影响主循环。"""


class LoopEventBus:
    """最小事件总线：按注册顺序分发，隔离 subscriber 异常（§7.2）。"""

    def __init__(self) -> None:
        self._subscribers: list[LoopEventSubscriber] = []
        # 被隔离的 subscriber 异常记录（(subscriber, event, exc)），供诊断与测试断言。
        self._isolated: list[tuple[LoopEventSubscriber, LoopEvent, Exception]] = []

    def subscribe(self, subscriber: LoopEventSubscriber) -> None:
        self._subscribers.append(subscriber)

    def publish(self, event: LoopEvent) -> None:
        """按注册顺序分发；任一 subscriber 抛错都被隔离，不打断其余分发与主循环。"""
        for subscriber in self._subscribers:
            try:
                subscriber.on_event(event)
            except Exception as exc:  # noqa: BLE001 - 有意隔离，防单个订阅者破坏主循环
                self._isolated.append((subscriber, event, exc))

    @property
    def isolated_failures(
        self,
    ) -> tuple[tuple[LoopEventSubscriber, LoopEvent, Exception], ...]:
        """被隔离的 subscriber 失败记录（诊断 / 测试用）。"""
        return tuple(self._isolated)
