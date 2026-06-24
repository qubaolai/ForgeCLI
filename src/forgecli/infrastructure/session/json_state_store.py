"""state.json 的原子写实现。

按 ``<sessions_dir>/<session_id>/state.json`` 落盘，读写都经 json_io
（原子替换 + 统一错误）。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.session.snapshot import SessionSnapshot
from forgecli.application.session.state_store import StateStore
from forgecli.infrastructure.session.json_io import read_json, write_json_atomic


class JsonStateStore(StateStore):
    """``sessions/<session_id>/state.json`` 的 JSON 实现。"""

    def __init__(self, sessions_dir: Path) -> None:
        self._root = sessions_dir

    def _file(self, session_id: str) -> Path:
        return self._root / session_id / "state.json"

    def write(self, snapshot: SessionSnapshot) -> None:
        write_json_atomic(self._file(snapshot.session_id), snapshot.to_dict())

    def read(self, session_id: str) -> SessionSnapshot | None:
        data = read_json(self._file(session_id))
        if data is None:
            return None
        return SessionSnapshot.from_dict(data)
