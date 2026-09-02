"""内容寻址的 artifact 存储: 落在 Forge 状态目录, 不落在工作区."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

from forgecli.application.tools.artifact_store import (
    ArtifactMissing,
    ArtifactStore,
    valid_artifact_id,
)
from forgecli.domain.tool.hashing import digest_text
from forgecli.domain.tool.result import ArtifactRef
from forgecli.infrastructure.json_io import write_atomic

__all__ = ["FsArtifactStore"]


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
