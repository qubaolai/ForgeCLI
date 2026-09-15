"""ArtifactStore: 工具输出的落盘位置 (ADR-0004 §8, ADR-0032 决策 6).

内容不落在工作区: 一次跑测试产生的 20MB 日志写进用户仓库, 既污染工作区又会被下一次
Agent 读回上下文. 它落在 Forge 状态目录, 事件里只留引用, 大小和哈希.

ADR-0032 起**每次工具调用的完整输出都落盘**, 不再只落超阈值的那部分:
``max_inline_bytes`` 从"存不存的开关"降级成"回填多少的开关". 一级降级要把 transcript
里的正文换成引用, 而那要求内容确实存在某处 —— 只存溢出部分的话, 占最多数的那批中小
输出根本没得降.

代价是磁盘, 由 ``sweep`` 按最后引用时间回收 (决策 6). 引用时间就是文件 mtime:
``write`` 在内容已存在时跳过写入, 所以 mtime 不会自己刷新, ``touch`` 是它唯一的续期
来源. 不另存引用计数 —— 一份独立的计数文件必须与 artifact 写入成对原子完成, 漏算会
删掉还在用的文件, 多算会永远删不掉, 两种错都是静默的.
"""

from __future__ import annotations

import contextlib
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

from forgecli.common.json_io import write_atomic
from forgecli.core.tool.hashing import digest_text
from forgecli.core.tool.result import ArtifactRef

__all__ = [
    "ARTIFACT_ID_PATTERN",
    "ARTIFACT_RETENTION_SECONDS",
    "ArtifactMissing",
    "ArtifactRef",
    "ArtifactStore",
    "FsArtifactStore",
    "valid_artifact_id",
]

# artifact_id 是内容 sha256 的十六进制前 16 位, 没有第二种形状.
#
# 这条校验不是防御性编程, 它是 ``artifact_read`` 那条链上唯一的一道闸 (ADR-0032
# 决策 6.2): 工具的 ToolPlan 不声明任何路径 target, 所以 workspace_analyzer 没有东西
# 可判 —— 而"目标集合在机制上封闭"这个证明成立的前提, 就是 id 不能表达路径.
# ``_root / ".." / "../../etc/passwd.txt"`` 会直接穿越出 artifacts 目录.
ARTIFACT_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

# 多久没被引用就回收 (ADR-0032 决策 6). 写在端口这一侧而不是实现里: 它是决策不是实现
# 细节, 换一个 ArtifactStore 实现不该顺带换掉保留期.
#
# 3 天的取舍: 删掉的代价多数情况下是重读一次文件, 比磁盘无限涨好. 但有两类内容重读
# 不回来 —— shell_run 的输出 (重跑不等于取回三天前那次, 而且会再执行一次子进程),
# 以及已经被二级摘要吃掉的原文. 所以一级降级只承诺"3 天内可回取", 不承诺无损.
ARTIFACT_RETENTION_SECONDS = 3 * 24 * 60 * 60


def valid_artifact_id(value: str) -> bool:
    return bool(ARTIFACT_ID_PATTERN.match(value))


class ArtifactMissing(KeyError):
    """取一个不存在的 artifact.

    与"id 写错了"分开是必要的: 3 天过期回收之后, 一条仍然指着它的降级占位会让模型去
    取一个已经不在的 id (决策 6.1). 模型读不出这是清理机制还是自己写错了, 而后一种
    理解会让它反复重试.
    """


class ArtifactStore(ABC):
    @abstractmethod
    def write(self, *, invocation_id: str, name: str, data: str) -> ArtifactRef:
        """落盘一段输出并返回引用. 内容寻址: 同一段内容只占一份空间."""

    @abstractmethod
    def read(
        self,
        artifact_id: str,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> str:
        """读回内容. offset 是起始行 (从 1 起), limit 是行数, 都不给则读全文.

        分段读不是锦上添花: 单份上限 32 MB, 整份读进内存正是要避免的事.
        找不到时抛 ``ArtifactMissing``.
        """

    @abstractmethod
    def exists(self, artifact_id: str) -> bool:
        """内容还在不在. 降级占位据此选"可取回"还是"已过期回收"."""

    @abstractmethod
    def touch(self, artifact_id: str) -> None:
        """把最后引用时间刷到现在. 不存在时静默返回 —— 续期一个已经被回收的内容
        不是错误, 调用方接着会拿 ``exists`` 走"已过期"那条分支."""

    @abstractmethod
    def sweep(self, *, older_than_seconds: float) -> int:
        """删掉超过给定时长没被引用过的内容, 返回删除数量."""


# ---- 原 infrastructure/tools/fs_artifact_store.py ----
# 内容寻址的 artifact 存储: 落在 Forge 状态目录, 不落在工作区.
class FsArtifactStore(ArtifactStore):
    """按内容哈希寻址: 同一段输出反复溢写只占一份空间.

    **文件 mtime 就是"最后一次被引用的时间"** (ADR-0032 决策 6). ``write`` 在内容已经
    存在时跳过写入, 所以 mtime 不会自己刷新 —— ``touch`` 与 ``read`` 是它唯一的续期
    来源, ``sweep`` 据此回收.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(self, *, invocation_id: str, name: str, data: str) -> ArtifactRef:
        content_hash = digest_text(data)
        artifact_id = content_hash.split(":", 1)[1][:16]
        target = self._path_of(artifact_id)
        if target.exists():
            # 同一段内容再次产生: 不重写文件, 但要续期 —— 它刚刚被用到了.
            self._refresh(target)
        else:
            write_atomic(target, data)
        return ArtifactRef(
            artifact_id=artifact_id,
            path=str(target),
            size=len(data.encode("utf-8")),
            content_hash=content_hash,
            truncated=False,
        )

    def read(
        self,
        artifact_id: str,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> str:
        target = self._path_of(artifact_id)
        if not target.exists():
            raise ArtifactMissing(artifact_id)
        self._refresh(target)
        if offset is None and limit is None:
            return target.read_text(encoding="utf-8")
        return self._window(target, offset=offset, limit=limit)

    def exists(self, artifact_id: str) -> bool:
        if not valid_artifact_id(artifact_id):
            return False
        return self._path_of(artifact_id).exists()

    def touch(self, artifact_id: str) -> None:
        if not valid_artifact_id(artifact_id):
            return
        target = self._path_of(artifact_id)
        if target.exists():
            self._refresh(target)

    def sweep(self, *, older_than_seconds: float) -> int:
        """删掉超期未引用的内容.

        扫描本身要能在任何情况下跑完: 一个读不了的子目录不该让整次回收停摆, 更不该
        让进程起不来 —— 这是启动路径上的一步.
        """
        if older_than_seconds <= 0:
            raise ValueError("older_than_seconds 必须为正")
        if not self._root.exists():
            return 0
        cutoff = time.time() - older_than_seconds
        removed = 0
        for path in self._root.glob("*/*.txt"):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                path.unlink()
            except OSError:
                continue
            removed += 1
        return removed

    # ---- 内部 ----

    def _path_of(self, artifact_id: str) -> Path:
        """id 一律先校验再拼路径.

        ``artifact_id`` 到了 ADR-0032 决策 6.2 之后是**模型可控**的 (``artifact_read``
        的入参). 不校验的话, ``".."`` 会让 ``self._root / artifact_id[:2]`` 直接跳出
        artifacts 目录, 而这条路径绕开的恰好是受保护路径那道 Hard Deny —— 工具内部走
        ArtifactStore, 不经 workspace_analyzer.
        """
        if not valid_artifact_id(artifact_id):
            raise ArtifactMissing(artifact_id)
        return self._root / artifact_id[:2] / f"{artifact_id}.txt"

    @staticmethod
    def _refresh(target: Path) -> None:
        """刷新最后引用时间.

        失败不抛: 续期不成的后果是早一轮被回收, 不是这次调用失败.
        """
        with contextlib.suppress(OSError):
            target.touch()

    @staticmethod
    def _window(target: Path, *, offset: int | None, limit: int | None) -> str:
        """按行取一段. 逐行读, 不把整份内容拉进内存 —— 单份上限 32 MB."""
        start = max(1, offset or 1)
        end = None if limit is None else start + max(0, limit) - 1
        picked: list[str] = []
        with target.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if number < start:
                    continue
                if end is not None and number > end:
                    break
                picked.append(line.rstrip("\n"))
        return "\n".join(picked)
