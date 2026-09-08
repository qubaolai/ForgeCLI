"""SessionCatalog 的文件系统实现：扫描 ``sessions/`` 下含 state.json 的子目录。

只把「有 state.json」当作一个可枚举的会话——半截创建（只有空目录、或只有
events.jsonl 还没写出快照）的不算，避免列出无法读取摘要的脏目录。

删除走 ``sessions/.trash/``: 先改名, 后台再删。目录名以 ``.`` 开头, 所以它自己不会
被列成一个会话; 名字里带着 session_id 与时间戳, 跨进程也认得出这份垃圾原本属于谁。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from forgecli.application.session.session_catalog import SessionCatalog, Tombstone

_TRASH = ".trash"
# session_id 与时间戳之间的分隔。session_id 形如 20260907T150001-ab12cd34, 里面有
# 短横也有大写, 但不会出现连续两个下划线 —— 所以用它拆回来是安全的。
_SEPARATOR = "__"


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

    def begin_delete(self, session_id: str) -> Tombstone | None:
        source = self._session_dir(session_id)
        if source is None or not source.is_dir():
            return None
        trash = self._sessions_root / _TRASH
        trash.mkdir(parents=True, exist_ok=True)
        name = f"{session_id}{_SEPARATOR}{time.time_ns()}"
        source.rename(trash / name)
        return Tombstone(id=name, session_id=session_id)

    def purge(self, tombstone: Tombstone) -> None:
        target = self._sessions_root / _TRASH / tombstone.id
        # 只删自己名下那一层, 且忽略"已经不在了": 上一次进程与这一次的清扫可能撞上,
        # 而两边都在删同一份垃圾不是错误。
        if target.parent.resolve() != (self._sessions_root / _TRASH).resolve():
            return
        shutil.rmtree(target, ignore_errors=True)

    def tombstones(self) -> list[Tombstone]:
        trash = self._sessions_root / _TRASH
        if not trash.is_dir():
            return []
        found: list[Tombstone] = []
        for child in trash.iterdir():
            if not child.is_dir():
                continue
            session_id = child.name.split(_SEPARATOR, 1)[0]
            found.append(Tombstone(id=child.name, session_id=session_id))
        return found

    def _session_dir(self, session_id: str) -> Path | None:
        """会话目录; session_id 指不到 ``sessions/`` 名下就返回 None。

        id 来自路由与命令行, 拼进路径之前必须确认它没有把目标指到外面去 —— 一个
        ``../..`` 就能变成挪走整个项目。
        """
        if not session_id or session_id in {".", ".."} or "/" in session_id:
            return None
        target = self._sessions_root / session_id
        if target.parent.resolve() != self._sessions_root.resolve():
            return None
        return target
