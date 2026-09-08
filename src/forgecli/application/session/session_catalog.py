"""历史会话枚举与删除抽象（由 infrastructure 实现）。

EventStore / StateStore 都按 session_id 寻址，无法回答「这个项目下有哪些会话」。
SessionCatalog 补上这一能力：只负责列出 session id，具体落在哪、怎么扫描隔离在
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

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Tombstone:
    """一个等待清理的会话目录。

    ``session_id`` 一起带着, 因为清理不只是删目录 —— 那个会话的恢复点也要跟着走, 而
    跨进程恢复清理时只剩下目录名可以依据。
    """

    id: str
    session_id: str


class SessionCatalog(ABC):
    """枚举某项目 ``sessions/`` 下的全部会话 id。"""

    @abstractmethod
    def list_session_ids(self) -> list[str]:
        """返回全部会话 id（顺序不保证，由上层排序）；无目录返回 []。"""

    @abstractmethod
    def begin_delete(self, session_id: str) -> Tombstone | None:
        """把会话目录挪进回收位, 返回墓碑; 会话不存在返回 None。

        整个目录一起挪, 不是逐个文件: 会话目录下不只有正文与快照, 还有处理过程与计划
        待办 (它们本来就按会话分区, 见 ``paths.plans_dir``)。逐个删的写法会在新增一种
        会话级数据时漏掉它, 而漏掉不会报错 —— 只会在项目目录里攒下没人认领的孤儿。
        """

    @abstractmethod
    def purge(self, tombstone: Tombstone) -> None:
        """真正删掉墓碑目录。已经不在了不算错 —— 清理可以被重复触发。"""

    @abstractmethod
    def tombstones(self) -> list[Tombstone]:
        """还没清完的墓碑。进程没跑完就退出时留下的, 下次启动接着清。"""
