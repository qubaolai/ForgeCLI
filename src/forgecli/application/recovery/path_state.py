"""恢复层共用的路径状态摘要。"""

from __future__ import annotations

from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.hashing import digest

__all__ = ["directory_content_hash"]


def directory_content_hash(path: str, context: ExecutionContext) -> str:
    """摘要直接子项；子目录自身 mtime 会把更深层的增删传播到这里。"""
    entries: list[dict[str, object]] = []
    for name in context.filesystem.list_dir(path):
        facts = context.filesystem.facts(f"{path.rstrip('/')}/{name}")
        entries.append(
            {
                "name": name,
                "kind": facts.kind.value,
                "identity": facts.file_identity,
                "size": facts.size,
                "mtime_ns": facts.mtime_ns,
                "mode": facts.mode,
                "symlink": facts.is_symlink,
                "link_target": facts.link_target,
            }
        )
    return digest({"directory_entries": entries})
