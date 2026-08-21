"""基于真实文件系统的只读视图.

version 只标识本次 ExecutionContext 使用的观察入口，不伪装成 OS 级快照。每次调用创建
新实例；分析读取过的脚本和可执行文件由 FileStateBinding 冻结身份与内容，并在 perform
前重验。glob 等动态集合则在审批后重新 prepare，不能靠一个时间戳声称文件系统已冻结。

realpath 一律解析: 受保护路径判定与目标集合封闭都依赖它, 字符串前缀匹配挡不住符号
链接与 macOS 的 /private 别名.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
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
            link_stat = target.lstat()
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
        try:
            # 身份、大小与 mtime 描述实际会被读取/执行的最终对象；链接本身另由
            # is_symlink/link_target 表达。混用 lstat 元数据和目标内容会让所有符号链接
            # 可执行文件都看起来像“短读”。
            object_stat = target.stat()
        except OSError:
            object_stat = link_stat

        return PathFacts(
            path=path,
            realpath=real,
            kind=_kind_of(target),
            size=object_stat.st_size,
            mtime_ns=object_stat.st_mtime_ns,
            file_identity=f"{object_stat.st_dev}:{object_stat.st_ino}",
            mode=object_stat.st_mode,
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

    def expand_glob(
        self, pattern: str, *, root: str, max_results: int | None = None
    ) -> tuple[str, ...]:
        base = Path(root)
        candidate = Path(pattern)
        if candidate.is_absolute():
            # 绝对 glob: 从根锚点展开, 保留用户写的那一段作为 pattern.
            anchor = Path(candidate.anchor)
            relative = candidate.relative_to(anchor)
            iterator = anchor.glob(str(relative))
            return _bounded_paths(iterator, max_results)
        try:
            return _bounded_paths(base.glob(pattern), max_results)
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


def _bounded_paths(paths: Iterable[Path], max_results: int | None) -> tuple[str, ...]:
    limit = max_results if max_results is not None and max_results >= 0 else None
    if limit == 0:
        return ()
    collected: list[str] = []
    for item in paths:
        collected.append(str(item))
        if limit is not None and len(collected) >= limit:
            break
    return tuple(sorted(collected))


def _kind_of(target: Path) -> PathKind:
    # 顺序有意义: symlink 先按最终目标归类, 链接本身的身份由 is_symlink 字段表达.
    if target.is_dir():
        return PathKind.DIRECTORY
    if target.is_file():
        return PathKind.FILE
    return PathKind.OTHER
