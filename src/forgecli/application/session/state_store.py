"""状态快照存储抽象（由 infrastructure 实现）。

约定：写入走临时文件 + 原子替换；无文件时 read 返回 None，不抛异常；
文件损坏由 infrastructure 翻成面向用户的 SessionStateError。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.session.snapshot import SessionSnapshot


class StateStore(ABC):
    """``sessions/<session_id>/state.json`` 的读写。"""

    @abstractmethod
    def write(self, snapshot: SessionSnapshot) -> None:
        """原子写出快照；首次写入会创建会话目录。"""

    @abstractmethod
    def read(self, session_id: str) -> SessionSnapshot | None:
        """读取快照；不存在返回 None。"""
