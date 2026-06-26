"""SessionCatalog 的文件系统实现：扫描 ``sessions/`` 下含 state.json 的子目录。

只把「有 state.json」当作一个可枚举的会话——半截创建（只有空目录、或只有
events.jsonl 还没写出快照）的不算，避免列出无法读取摘要的脏目录。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.session.session_catalog import SessionCatalog


class FsSessionCatalog(SessionCatalog):
    """``<sessions_dir>/<session_id>/state.json`` 的目录枚举实现。"""

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_root = sessions_dir

    def list_session_ids(self) -> list[str]:
        if not self._sessions_root.exists():
            return []

        return [
            child.name
            for child in self._sessions_root.iterdir()
            if child.is_dir() and (child / "state.json").exists()
        ]
