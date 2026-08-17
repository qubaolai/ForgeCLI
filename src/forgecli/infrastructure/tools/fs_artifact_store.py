"""内容寻址的产物存储: 落在 Forge 状态目录, 不落在工作区."""

from __future__ import annotations

from pathlib import Path

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.domain.tool.hashing import digest_text
from forgecli.domain.tool.result import ArtifactRef

__all__ = ["FsArtifactStore"]


class FsArtifactStore(ArtifactStore):
    """按内容哈希寻址: 同一段输出反复溢写只占一份空间."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(self, *, invocation_id: str, name: str, data: str) -> ArtifactRef:
        content_hash = digest_text(data)
        artifact_id = content_hash.split(":", 1)[1][:16]
        target = self._path_of(artifact_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            # 同名先写临时文件再改名: 半截产物不该被别的调用读到.
            temp = target.with_suffix(".partial")
            temp.write_text(data, encoding="utf-8")
            temp.replace(target)
        return ArtifactRef(
            artifact_id=artifact_id,
            path=str(target),
            size=len(data.encode("utf-8")),
            content_hash=content_hash,
            truncated=False,
        )

    def read(self, artifact_id: str) -> str:
        target = self._path_of(artifact_id)
        if not target.exists():
            raise KeyError(f"产物不存在: {artifact_id}")
        return target.read_text(encoding="utf-8")

    def _path_of(self, artifact_id: str) -> Path:
        return self._root / artifact_id[:2] / f"{artifact_id}.txt"
