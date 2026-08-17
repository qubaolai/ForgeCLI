"""基于真实文件系统的只读视图.

version 由构造时的时间戳与工作区根决定: 视图对象一经创建就代表"那一刻的文件系统",
需要看到新变化的调用方应当创建新视图, 而不是指望同一个视图自己刷新 —— 后者会让
prepare 展开的目标集合与执行时的实际目标悄悄分叉.

realpath 一律解析: 受保护路径判定与目标集合封闭都依赖它, 字符串前缀匹配挡不住符号
链接与 macOS 的 /private 别名.
"""

from __future__ import annotations

import time
from pathlib import Path

from forgecli.application.workspace.filesystem_view import (
    FileSystemView,
    PathFacts,
    PathKind,
)

__all__ = ["OsFileSystemView"]


class OsFileSystemView(FileSystemView):
    def __init__(self, *, version: str | None = None) -> None:
        self._version = version or f"fs-{time.time_ns()}"

    @property
    def version(self) -> str:
        return self._version

    def facts(self, path: str) -> PathFacts:
        target = Path(path)
        try:
            stat = target.lstat()
        except OSError:
            # 对象还不存在. realpath 仍然要算 —— 只是要靠**已存在的父目录**去算.
            # 早先这里直接回字面路径, 于是工作区里一个 `link -> ~/.ssh` 加上写入
            # `link/new_file` 就被判成工作区内路径, 而真正的写入会跟着父目录的
            # 符号链接落到 ~/.ssh 里.
            return PathFacts(
                path=path, realpath=_resolved_parent(target), kind=PathKind.MISSING
            )

        is_symlink = target.is_symlink()
        try:
            real = str(target.resolve(strict=False))
        except OSError:
            real = path
        link_target = None
        if is_symlink:
            try:
                link_target = str(target.readlink())
            except OSError:
                link_target = None

        return PathFacts(
            path=path,
            realpath=real,
            kind=_kind_of(target),
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            file_identity=f"{stat.st_dev}:{stat.st_ino}",
            mode=stat.st_mode,
            is_symlink=is_symlink,
            link_target=link_target,
        )

    def read_text(self, path: str, *, max_bytes: int) -> str:
        return self.read_bytes(path, max_bytes=max_bytes).decode(
            "utf-8", errors="replace"
        )

    def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
        try:
            with Path(path).open("rb") as handle:
                return handle.read(max_bytes)
        except OSError:
            return b""

    def list_dir(self, path: str) -> tuple[str, ...]:
        try:
            return tuple(sorted(entry.name for entry in Path(path).iterdir()))
        except OSError:
            return ()

    def expand_glob(self, pattern: str, *, root: str) -> tuple[str, ...]:
        base = Path(root)
        candidate = Path(pattern)
        if candidate.is_absolute():
            # 绝对 glob: 从根锚点展开, 保留用户写的那一段作为 pattern.
            anchor = Path(candidate.anchor)
            relative = candidate.relative_to(anchor)
            return tuple(sorted(str(item) for item in anchor.glob(str(relative))))
        try:
            return tuple(sorted(str(item) for item in base.glob(pattern)))
        except (OSError, ValueError):
            return ()


def _resolved_parent(target: Path) -> str:
    """把不存在的对象锚到最近的已存在祖先上, 再拼回剩下的段.

    `Path.resolve(strict=False)` 已经会解析已存在的那一段, 这里额外兜住它抛错的情形
    (循环链接, 权限不足): 那时宁可返回字面路径也不能返回空.
    """
    try:
        return str(target.resolve(strict=False))
    except OSError:
        return str(target)


def _kind_of(target: Path) -> PathKind:
    # 顺序有意义: symlink 先按最终目标归类, 链接本身的身份由 is_symlink 字段表达.
    if target.is_dir():
        return PathKind.DIRECTORY
    if target.is_file():
        return PathKind.FILE
    return PathKind.OTHER
