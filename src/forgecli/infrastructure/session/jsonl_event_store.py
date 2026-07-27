"""events.jsonl 的 append-only 实现。

每行一个独立 JSON event，按 ``<sessions_dir>/<session_id>/events.jsonl`` 落盘。
追加用文件 ``"a"`` 模式，绝不重写既有行。
"""

from __future__ import annotations

import json
from pathlib import Path

from forgecli.application.session.event_store import EventStore
from forgecli.domain.session.events import SessionEvent


class JsonlEventStore(EventStore):
    """``sessions/<session_id>/events.jsonl`` 的 JSONL 实现。

    构造时只给定 sessions 根目录，按 session_id 拼出每个会话的事件文件路径。
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._root = sessions_dir

    def _file(self, session_id: str) -> Path:
        return self._root / session_id / "events.jsonl"

    def append(self, event: SessionEvent) -> None:
        path = self._file(event.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read(self, session_id: str) -> list[SessionEvent]:
        path = self._file(session_id)
        if not path.exists():
            return []
        events: list[SessionEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            data = json.loads(text)
            if isinstance(data, dict):
                events.append(SessionEvent.from_dict(data))
        return events
