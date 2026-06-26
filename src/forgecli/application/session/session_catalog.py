"""历史会话枚举抽象（由 infrastructure 实现）。

EventStore / StateStore 都按 session_id 寻址，无法回答「这个项目下有哪些会话」。
SessionCatalog 补上这一能力：只负责列出 session id，具体落在哪、怎么扫描隔离在
infrastructure。约定：无 sessions 目录时返回 []，不抛异常。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SessionCatalog(ABC):
    """枚举某项目 ``sessions/`` 下的全部会话 id。"""

    @abstractmethod
    def list_session_ids(self) -> list[str]:
        """返回全部会话 id（顺序不保证，由上层排序）；无目录返回 []。"""
