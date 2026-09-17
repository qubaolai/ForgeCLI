"""file_write / file_edit 真正落盘的那几个动作.

只放**机制**: 怎么把一段文本原子地写成一个文件. 写不写, 写哪里, 由 prepare 算出并经
裁决; 这里的函数拿到的已经是绝对路径与最终内容.

新建与覆写是两个函数, 不能共用: 新建要让内核原子拒绝已存在的目标, 覆写要保留原文件的
mode. 合成一个之后, 一次"新建"会静默覆盖同名文件.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path

__all__ = ["create_file", "make_directory", "replace_file"]


def replace_file(path: str, content: str) -> None:
    """尽可能跨平台地原子替换文本文件: 目标要么保持旧内容, 要么变成完整的新内容.

    Windows 上若目标文件被其他程序以不允许删除/重命名的方式打开, os.replace()
    仍可能失败.
    """
    target = Path(path)
    parent = target.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"父目录不存在或不是目录: {parent}")
    before = target.lstat()
    if stat.S_ISLNK(before.st_mode):
        raise OSError(f"拒绝替换符号链接: {target}")

    fd = -1
    temp_path: str | None = None
    try:
        fd, temp_path = tempfile.mkstemp(
            dir=parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        if hasattr(os, "fchmod"):
            os.fchmod(fd, stat.S_IMODE(before.st_mode))
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            fd = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # 同一文件系统内用新文件原子替换旧路径.
        os.replace(temp_path, target)
        temp_path = None
        _sync_directory(parent)
    finally:
        if fd != -1:
            os.close(fd)
        if temp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)


def create_file(path: str, content: str) -> None:
    """用硬链接发布完整临时文件, 目标已存在时由内核原子拒绝."""
    target = Path(path)
    parent = target.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"父目录不存在或不是目录: {parent}")
    fd = -1
    temp_path: str | None = None
    try:
        fd, temp_path = tempfile.mkstemp(
            dir=parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            fd = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp_path, target)
        os.unlink(temp_path)
        temp_path = None
        _sync_directory(parent)
    finally:
        if fd != -1:
            os.close(fd)
        if temp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)


def make_directory(path: str) -> None:
    """建一级目录. 已存在不算错.

    parents 保持默认的 False: 要建的每一级都由 prepare 逐级列进 write_paths 并经过
    裁决, parents=True 会创建没被声明过的祖先目录.
    """
    Path(path).mkdir(exist_ok=True)


def _sync_directory(directory: Path) -> None:
    """POSIX 上把目录项落盘. Windows 不支持以这种方式 fsync 目录."""
    if os.name == "nt":
        return
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
