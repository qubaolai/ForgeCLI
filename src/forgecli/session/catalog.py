"""历史会话枚举与删除抽象（由 infrastructure 实现）。

JsonlEventStore / JsonStateStore 都按 session_id 寻址，无法回答「这个项目下有哪些
会话」。
FsSessionCatalog 补上这一能力：只负责列出 session id，具体落在哪、怎么扫描隔离在
infrastructure。约定：无 sessions 目录时返回 []，不抛异常。

**删除分两步**，因为它们的耗时差着数量级 (ADR-0048 决策 7 的同一条判断: 别让一次
交互等在一堆文件 I/O 上):

- ``begin_delete`` 把会话目录挪进回收位。一次改名, 与会话有多大无关, 所以它可以留在
  请求里。做完这一步会话立刻从 ``list_session_ids`` 消失 —— 用户删完刷新就看不见它了。
- ``purge`` 才真正删文件。慢, 交给后台。

不直接在后台 rmtree 原目录: 那样删到一半的会话仍然带着 ``state.json``, 会被列成一个
读不出内容的坏会话; 进程要是死在中途, 留下的东西也分不清是垃圾还是损坏。改名之后,
留下的一定是垃圾, 下次启动扫一遍就能接着清。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Tombstone:
    """一个等待清理的会话目录。

    ``session_id`` 一起带着, 因为清理不只是删目录 —— 那个会话的恢复点也要跟着走, 而
    跨进程恢复清理时只剩下目录名可以依据。
    """

    id: str
    session_id: str


# ---- 原 infrastructure/session/fs_session_catalog.py ----
# FsSessionCatalog 的文件系统实现：扫描 ``sessions/`` 下含 state.json 的子目录。
#
# 只把「有 state.json」当作一个可枚举的会话——半截创建（只有空目录、或只有
# events.jsonl 还没写出快照）的不算，避免列出无法读取摘要的脏目录。
#
# 删除走 ``sessions/.trash/``: 先改名, 后台再删。目录名以 ``.`` 开头, 所以它自己不会
# 被列成一个会话; 名字里带着 session_id 与时间戳, 跨进程也认得出这份垃圾原本属于谁。
_TRASH = ".trash"
# session_id 与时间戳之间的分隔。session_id 形如 20260907T150001-ab12cd34, 里面有
# 短横也有大写, 但不会出现连续两个下划线 —— 所以用它拆回来是安全的。
_SEPARATOR = "__"


class FsSessionCatalog:
    """``<sessions_dir>/<session_id>/state.json`` 的目录枚举实现。"""

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_root = sessions_dir

    def list_session_ids(self) -> list[str]:
        """返回全部会话 id（顺序不保证，由上层排序）；无目录返回 []。"""
        if not self._sessions_root.exists():
            return []

        return [
            child.name
            for child in self._sessions_root.iterdir()
            if child.is_dir() and (child / "state.json").exists()
        ]

    def begin_delete(self, session_id: str) -> Tombstone | None:
        """把会话目录挪进回收位, 返回墓碑; 会话不存在返回 None。

        整个目录一起挪, 不是逐个文件: 会话目录下不只有正文与快照, 还有处理过程与计划
        待办 (它们本来就按会话分区, 见 ``paths.plans_dir``)。逐个删的写法会在新增一种
        会话级数据时漏掉它, 而漏掉不会报错 —— 只会在项目目录里攒下没人认领的孤儿。
        """
        source = self._session_dir(session_id)
        if source is None or not source.is_dir():
            return None
        trash = self._sessions_root / _TRASH
        trash.mkdir(parents=True, exist_ok=True)
        name = f"{session_id}{_SEPARATOR}{time.time_ns()}"
        source.rename(trash / name)
        return Tombstone(id=name, session_id=session_id)

    def purge(self, tombstone: Tombstone) -> None:
        """真正删掉墓碑目录。已经不在了不算错 —— 清理可以被重复触发。"""
        target = self._sessions_root / _TRASH / tombstone.id
        # 只删自己名下那一层, 且忽略"已经不在了": 上一次进程与这一次的清扫可能撞上,
        # 而两边都在删同一份垃圾不是错误。
        if target.parent.resolve() != (self._sessions_root / _TRASH).resolve():
            return
        shutil.rmtree(target, ignore_errors=True)

    def tombstones(self) -> list[Tombstone]:
        """还没清完的墓碑。进程没跑完就退出时留下的, 下次启动接着清。"""
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
