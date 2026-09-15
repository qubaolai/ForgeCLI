"""``sessions/<session_id>/state.json`` 的读写.

约定: 写入走临时文件 + 原子替换; 无文件时 read 返回 None, 不抛异常; 文件损坏翻成面向
用户的 SessionStateError, 这是 tui / web 的会话命令 catch 的那个类型.
"""

from __future__ import annotations

from pathlib import Path

from forgecli.common.errors import ConfigReadError, SessionStateError
from forgecli.common.json_io import read_document, write_document
from forgecli.session.snapshot import SessionSnapshot


class JsonStateStore:
    """``sessions/<session_id>/state.json`` 的 JSON 实现。"""

    def __init__(self, sessions_dir: Path) -> None:
        self._root = sessions_dir

    def _file(self, session_id: str) -> Path:
        return self._root / session_id / "state.json"

    def write(self, snapshot: SessionSnapshot) -> None:
        """原子写出快照；首次写入会创建会话目录。"""
        write_document(self._file(snapshot.session_id), snapshot.to_dict())

    def read(self, session_id: str) -> SessionSnapshot | None:
        """读取快照；不存在返回 None。"""
        path = self._file(session_id)
        if not path.exists():
            return None
        try:
            data = read_document(path, label="会话状态文件")
        except ConfigReadError as exc:
            raise SessionStateError(str(exc)) from exc
        return SessionSnapshot.from_dict(data)
