"""项目排他锁的行为约定 (ADR-0040 决策 4.5 迁移到 filelock 之前没有任何用例).

这把锁守的是"同一个项目只跑一个 forge". 它坏掉的方式不会有人立刻发现: 两个进程同时写
同一个项目的会话与恢复数据, 表现是偶发的状态错乱, 而不是一条报错. 所以换实现之前先把
约定写成用例 —— 换的是底下那把 OS 锁, 上面这几条一个字都不该变.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forgecli.infrastructure.project.process_lock import (
    ProcessLock,
    ProjectLockedError,
)


def _lock_path(tmp_path: Path) -> Path:
    # 刻意指一个还不存在的子目录: 首次启动时 projects/<id>/ 就是不存在的.
    return tmp_path / "projects" / "abc123" / "forge.lock"


def test_acquire_creates_parent_directory(tmp_path: Path) -> None:
    path = _lock_path(tmp_path)
    assert not path.parent.exists()
    lock = ProcessLock(path)
    lock.acquire()
    try:
        assert path.parent.is_dir()
    finally:
        lock.release()


def test_second_instance_is_refused(tmp_path: Path) -> None:
    """互斥是这把锁的全部意义: 第二个持有者必须拿不到."""
    path = _lock_path(tmp_path)
    first = ProcessLock(path)
    first.acquire()
    try:
        with pytest.raises(ProjectLockedError):
            ProcessLock(path).acquire()
    finally:
        first.release()


def test_release_lets_the_next_one_in(tmp_path: Path) -> None:
    path = _lock_path(tmp_path)
    first = ProcessLock(path)
    first.acquire()
    first.release()
    second = ProcessLock(path)
    second.acquire()  # 不抛就是通过
    second.release()


def test_repeated_acquire_is_idempotent(tmp_path: Path) -> None:
    """重复 acquire 不加计数, 所以一次 release 就真的放开.

    这条是 filelock 的默认行为**不**成立的地方: 它按重入计数, acquire 两次要 release
    两次. 上层没有配对调用的约定 (bootstrap 是 acquire 一次, 退出时 release 一次),
    所以这里必须把计数压平.
    """
    path = _lock_path(tmp_path)
    lock = ProcessLock(path)
    lock.acquire()
    lock.acquire()
    lock.release()

    other = ProcessLock(path)
    other.acquire()
    other.release()


def test_release_without_acquire_is_noop(tmp_path: Path) -> None:
    ProcessLock(_lock_path(tmp_path)).release()


def test_context_manager_releases(tmp_path: Path) -> None:
    path = _lock_path(tmp_path)
    with ProcessLock(path), pytest.raises(ProjectLockedError):
        ProcessLock(path).acquire()
    ProcessLock(path).acquire()


def test_busy_message_names_the_holder(tmp_path: Path) -> None:
    """被占用时要说得出是谁 —— 否则用户只能猜为什么起不来."""
    path = _lock_path(tmp_path)
    holder = ProcessLock(path)
    holder.acquire()
    try:
        with pytest.raises(ProjectLockedError) as caught:
            ProcessLock(path).acquire()
    finally:
        holder.release()
    assert caught.value.pid == os.getpid()
    assert str(os.getpid()) in caught.value.message


def test_holder_metadata_is_cleared_on_release(tmp_path: Path) -> None:
    path = _lock_path(tmp_path)
    holder_path = path.with_name(f"{path.name}.holder.json")
    lock = ProcessLock(path)
    lock.acquire()
    assert json.loads(holder_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    lock.release()
    assert not holder_path.exists()


def test_stale_holder_metadata_does_not_block(tmp_path: Path) -> None:
    """崩溃留下的元数据不是锁. 判据是"锁被不被持有", 不是"文件在不在"."""
    path = _lock_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_name(f"{path.name}.holder.json").write_text(
        json.dumps({"pid": 999999, "started_at": "2020-01-01", "host": "dead"}),
        encoding="utf-8",
    )
    lock = ProcessLock(path)
    lock.acquire()  # 不抛就是通过
    lock.release()


def test_unreadable_holder_metadata_still_reports_busy(tmp_path: Path) -> None:
    """元数据只服务诊断: 读不出来也不能把"被占用"说成"空闲"."""
    path = _lock_path(tmp_path)
    holder = ProcessLock(path)
    holder.acquire()
    holder_path = path.with_name(f"{path.name}.holder.json")
    holder_path.write_text("{ 不是 JSON", encoding="utf-8")
    try:
        with pytest.raises(ProjectLockedError) as caught:
            ProcessLock(path).acquire()
    finally:
        holder.release()
    assert caught.value.pid is None
    assert "正占用" in caught.value.message


def test_different_projects_do_not_collide(tmp_path: Path) -> None:
    first = ProcessLock(tmp_path / "projects" / "one" / "forge.lock")
    second = ProcessLock(tmp_path / "projects" / "two" / "forge.lock")
    first.acquire()
    second.acquire()  # 不同项目互不相干
    first.release()
    second.release()
