"""事件存储抽象（由 infrastructure 实现）。

命名沿用既有 ``*_store`` 约定：以「存储」表达业务意义，把「落在哪里、什么格式」
隔离在 application 之外。约定：append-only 追加；无文件时 read 返回 []，不抛异常。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.application.session.events import SessionEvent


class EventStore(ABC):
    """``sessions/<session_id>/events.jsonl`` 的追加与读取。"""

    @abstractmethod
    def append(self, event: SessionEvent) -> None:
        """追加一个事件（append-only）。首次写入会创建会话目录。"""

    @abstractmethod
    def read(self, session_id: str) -> list[SessionEvent]:
        """按追加顺序读回全部事件；无文件返回 []。"""
