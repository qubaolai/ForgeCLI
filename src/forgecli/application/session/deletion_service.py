"""删除一个会话及其全部会话级数据 (ADR-0001 / ADR-0015 / ADR-0022)。

**会跟着一起删的:**

- 会话快照 ``state.json`` 与会话正文 ``events.jsonl``
- 处理过程 ``runs.jsonl`` (展示数据, ADR-0016 §9)
- 计划与待办 ``plans/`` —— 它们本来就按会话分区 (ADR-0022 §2), 所以删目录就带走了
- 这个会话产生的恢复点, 连同它们的写时复制快照与不再被引用的旧内容 blob

**不会删的, 以及为什么:**

- **工具输出归档** (``state/artifacts/``): 内容寻址且**全局共享** —— 同一段输出在两个
  会话里只有一份文件, 没有会话归属可言。按会话删它等于删掉别人正在引用的东西。它有
  自己的回收机制 (按 mtime 的 TTL sweep, ADR-0032 决策 6)。
- **项目记忆** (``projects/<id>/memory.json``): 明确不按会话分区 —— 记忆只记"脱离对话
  之后仍然成立的事实" (ADR-0033 决策 5), 那种东西本来就该跨会话活着。
- **学习到的授权规则** (``state/rules/``): 按 workspace 分区的安全规则, 不是会话内容。
  删会话不该悄悄放宽或收紧下一个会话的授权。
- **工作区里的文件**: 会话删了, 它做过的改动还在。删的是记录, 不是成果。

最后一条的代价要说清楚: 恢复点一起删掉之后, **那个会话做过的改动就撤不回来了**。这是
删除的应有之义 (恢复点就是会话记录的一部分), 但界面必须在删之前讲明白。

## 为什么分成两段

真正删文件是慢的, 而且慢得没有上界: 写时复制快照是整棵工作区的克隆, 释放它等于
rmtree 一份仓库; blob 回收要扫过这个 workspace 的全部清单与全部内容文件。把这些放进
一次请求里, 一个跑了很久的项目足以让它超时。

所以 ``begin`` 只做一次改名 —— 与会话有多大无关, 做完会话立刻从列表消失; ``purge``
承担全部文件 I/O, 由调用方放到后台。中间进程死掉也不会留下模棱两可的东西: 墓碑一定
是垃圾, ``pending`` 把它捡回来接着清。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.application.recovery.coordinator import WorkspaceMutationCoordinator
from forgecli.application.session.session_catalog import SessionCatalog, Tombstone
from forgecli.application.session.state_store import StateStore
from forgecli.shared.errors import SessionStateError
from forgecli.shared.observability.log import get_log

__all__ = ["DeletedSession", "SessionDeletionService"]

_log = get_log(__name__)


@dataclass(frozen=True)
class DeletedSession:
    """删掉了哪个会话。

    不带"清掉了几个恢复点": 那要等后台清完才知道, 而这个值是在请求里返回的。报一个
    还没发生的数字, 不如不报。
    """

    session_id: str
    title: str


class SessionDeletionService:
    """删一个会话。它不认识"当前会话", 也不认识线程 —— 那都是组合根的事。"""

    def __init__(
        self,
        catalog: SessionCatalog,
        state_store: StateStore,
        *,
        workspace_id: str,
        recovery: WorkspaceMutationCoordinator | None = None,
    ) -> None:
        self._catalog = catalog
        self._states = state_store
        self._workspace_id = workspace_id
        # 缺省为 None: 没接恢复层时照常删会话目录, 只是没有恢复点要清。
        self._recovery = recovery

    def begin(self, session_id: str) -> tuple[DeletedSession, Tombstone]:
        """把会话挪进回收位并返回墓碑; 不存在抛 SessionStateError。

        这一步必须快: 它在请求里。一次改名, 与会话攒了多少东西无关。
        """
        snapshot = self._states.read(session_id)
        if snapshot is None:
            raise SessionStateError(f"未找到会话 {session_id}。")
        tombstone = self._catalog.begin_delete(session_id)
        if tombstone is None:
            raise SessionStateError(f"会话 {session_id} 的目录不存在, 无法删除。")
        _log.info("session.delete_started", session=session_id, tombstone=tombstone.id)
        return DeletedSession(session_id=session_id, title=snapshot.title), tombstone

    def purge(self, tombstone: Tombstone) -> int:
        """真正清掉一个墓碑: 恢复点先走, 再删目录。返回清掉的恢复点数。

        顺序有意义: **先清恢复点, 再删墓碑目录**。反过来的话, 中途失败会留下一批恢复
        点, 而记着它们归属的那个墓碑已经不在了 —— 谁也不会再去清它们。

        整个过程吞异常并如实记日志: 它跑在后台, 抛出去没有人接得住; 而清不干净的后果
        是占着磁盘, 不是数据不对 —— 下一次启动的扫描还会再试一遍。
        """
        removed = 0
        try:
            if self._recovery is not None:
                removed = self._recovery.discard_session(
                    self._workspace_id, tombstone.session_id
                )
            self._catalog.purge(tombstone)
        except Exception as error:  # noqa: BLE001 - 后台边界, 见 docstring
            _log.exception(
                "session.purge_failed",
                session=tombstone.session_id,
                tombstone=tombstone.id,
                error=type(error).__name__,
                message=str(error),
            )
            return removed
        _log.info(
            "session.purged",
            session=tombstone.session_id,
            tombstone=tombstone.id,
            checkpoints_removed=removed,
        )
        return removed

    def pending(self) -> list[Tombstone]:
        """上一次没清完的墓碑。启动时捡回来接着清。"""
        return self._catalog.tombstones()
