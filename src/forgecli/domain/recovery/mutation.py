"""变更集: 一次事务实际改了什么 (ADR-0015 §5).

两条容易搞混的口径:

- **预测写集合不是实际变更集合.** 预测只用来选快照策略; MutationSet 记录的是真的发生了
  什么. 把预测当成实际, 恢复时就会去还原一个从没被改过的文件, 而漏掉真正被改的那个.
- **触发恢复的是变更语义, 不是命令名.** `rm` 不必然要整区快照, `python` 也不能因为不在
  危险命令清单里就绕过屏障. 判据是"这个对象的旧状态还能不能从新状态推出来".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "ConflictStatus",
    "MutationEntry",
    "MutationSet",
    "ObjectType",
    "Operation",
    "Recoverability",
    "is_destructive",
]


class ObjectType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    MISSING = "missing"


class Operation(Enum):
    """对象上发生的操作."""

    CREATE = "create"
    OVERWRITE = "overwrite"
    APPEND = "append"
    TRUNCATE = "truncate"
    DELETE = "delete"
    MOVE = "move"
    REPLACE = "replace"
    METADATA = "metadata"
    LINK_RETARGET = "link_retarget"


class Recoverability(Enum):
    FULL = "full"  # preimage 已保存, 可完整还原
    CREATE_ONLY = "create_only"  # 原先不存在, 撤销即删除
    NONE = "none"  # 无法还原 (用户明示的一次性无恢复执行)


class ConflictStatus(Enum):
    CLEAN = "clean"
    CONFLICTED = "conflicted"  # 当前状态与记录的 postimage 不一致


# 会让旧状态无法仅从执行后状态推出来的操作 (ADR-0015 §2).
_DESTRUCTIVE = frozenset(
    {
        Operation.OVERWRITE,
        Operation.APPEND,
        Operation.TRUNCATE,
        Operation.DELETE,
        Operation.MOVE,
        Operation.REPLACE,
        Operation.METADATA,
        Operation.LINK_RETARGET,
    }
)


def is_destructive(operation: Operation, *, existed_before: bool) -> bool:
    """这次操作要不要保存 preimage.

    纯创建不用存内容, 但仍然要记一条轻量条目 —— 撤销时得知道"这个文件本来不存在".
    追加也算破坏性: 追加之后原文件长度这个信息就丢了.
    """
    if not existed_before:
        return False
    return operation in _DESTRUCTIVE


@dataclass(frozen=True)
class MutationEntry:
    """单个对象的变更记录 (ADR-0015 §5)."""

    relative_path: str
    object_type: ObjectType
    existed_before: bool
    operation: Operation
    preimage_content_hash: str | None = None
    preimage_metadata_hash: str | None = None
    postimage_content_hash: str | None = None
    postimage_metadata_hash: str | None = None
    source_path: str | None = None
    target_path: str | None = None
    file_identity: str = ""
    mode: int = 0
    link_target: str | None = None
    recoverability: Recoverability = Recoverability.FULL
    conflict_status: ConflictStatus = ConflictStatus.CLEAN
    regenerable: bool = False

    @property
    def needs_preimage(self) -> bool:
        return is_destructive(self.operation, existed_before=self.existed_before)


@dataclass(frozen=True)
class MutationSet:
    """一次事务的规范化变更集合."""

    entries: tuple[MutationEntry, ...] = field(default=())

    def with_entry(self, entry: MutationEntry) -> MutationSet:
        """同一路径只保留最初那条 preimage (ADR-0015 §4).

        事务内反复改同一个文件时, 第一次的旧内容才是要还原的那份; 后面几次的 preimage
        是中间态, 存了反而会把用户还原到一个从没存在过的版本.
        """
        existing = self.find(entry.relative_path)
        if existing is None:
            return MutationSet(entries=(*self.entries, entry))
        merged = MutationEntry(
            relative_path=existing.relative_path,
            object_type=existing.object_type,
            existed_before=existing.existed_before,
            operation=existing.operation,
            preimage_content_hash=existing.preimage_content_hash,
            preimage_metadata_hash=existing.preimage_metadata_hash,
            # postimage 取最新: 恢复前的冲突检查要跟"最后一次写完的样子"比.
            postimage_content_hash=entry.postimage_content_hash,
            postimage_metadata_hash=entry.postimage_metadata_hash,
            source_path=existing.source_path,
            target_path=entry.target_path or existing.target_path,
            file_identity=entry.file_identity or existing.file_identity,
            mode=existing.mode,
            link_target=entry.link_target or existing.link_target,
            recoverability=existing.recoverability,
            conflict_status=entry.conflict_status,
            regenerable=existing.regenerable,
        )
        return MutationSet(
            entries=tuple(
                merged if item.relative_path == entry.relative_path else item
                for item in self.entries
            )
        )

    def find(self, relative_path: str) -> MutationEntry | None:
        return next(
            (item for item in self.entries if item.relative_path == relative_path),
            None,
        )

    @property
    def created(self) -> tuple[MutationEntry, ...]:
        return tuple(item for item in self.entries if not item.existed_before)

    @property
    def modified(self) -> tuple[MutationEntry, ...]:
        return tuple(
            item
            for item in self.entries
            if item.existed_before and item.operation is not Operation.DELETE
        )

    @property
    def deleted(self) -> tuple[MutationEntry, ...]:
        return tuple(
            item for item in self.entries if item.operation is Operation.DELETE
        )

    def __len__(self) -> int:
        return len(self.entries)
