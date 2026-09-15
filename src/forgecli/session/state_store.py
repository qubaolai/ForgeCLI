"""状态快照存储抽象（由 infrastructure 实现）。

约定：写入走临时文件 + 原子替换；无文件时 read 返回 None，不抛异常；
文件损坏由 infrastructure 翻成面向用户的 SessionStateError。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.common.json_io import read_json, write_json_atomic
from forgecli.session.snapshot import SessionSnapshot


# ---- 原 infrastructure/session/json_state_store.py ----
# state.json 的原子写实现。
#
# 按 ``<sessions_dir>/<session_id>/state.json`` 落盘，读写都经 json_io
# （原子替换 + 统一错误）。
class JsonStateStore:
    """``sessions/<session_id>/state.json`` 的 JSON 实现。"""

    def __init__(self, sessions_dir: Path) -> None:
        self._root = sessions_dir

    def _file(self, session_id: str) -> Path:
        return self._root / session_id / "state.json"

    def write(self, snapshot: SessionSnapshot) -> None:
        """原子写出快照；首次写入会创建会话目录。"""
        write_json_atomic(self._file(snapshot.session_id), snapshot.to_dict())

    def read(self, session_id: str) -> SessionSnapshot | None:
        """读取快照；不存在返回 None。"""
        data = read_json(self._file(session_id))
        if data is None:
            return None
        return SessionSnapshot.from_dict(data)
