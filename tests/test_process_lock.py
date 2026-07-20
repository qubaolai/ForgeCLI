"""项目级排他锁 ProcessLock 的验收测试。

同进程内两个独立 fd 经 OS 咨询锁即可互斥（无需起子进程）：第一个 acquire 成功，
第二个在同路径 acquire 抛 ProjectLockedError；release 后可重取。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forgecli.infrastructure.project import ProcessLock, ProjectLockedError


def test_second_acquire_on_same_path_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "proj" / "forge.lock"  # 父目录不存在：acquire 应自行创建
    first = ProcessLock(path)
    first.acquire()
    try:
        with pytest.raises(ProjectLockedError):
            ProcessLock(path).acquire()
    finally:
        first.release()


def test_release_allows_reacquire(tmp_path: Path) -> None:
    path = tmp_path / "forge.lock"
    lock = ProcessLock(path)
    lock.acquire()
    lock.release()

    again = ProcessLock(path)
    again.acquire()  # 释放后另一个实例应能重新取得
    again.release()


def test_context_manager_acquires_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "forge.lock"
    with ProcessLock(path), pytest.raises(ProjectLockedError):
        ProcessLock(path).acquire()

    # 退出 with 后锁已释放，可再次取得。
    reacquired = ProcessLock(path)
    reacquired.acquire()
    reacquired.release()


def test_repeated_acquire_on_same_instance_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "forge.lock"
    lock = ProcessLock(path)
    lock.acquire()
    lock.acquire()  # 同一实例重复 acquire 不应抛错
    lock.release()


def test_holder_metadata_written_for_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "forge.lock"
    lock = ProcessLock(path)
    lock.acquire()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert "started_at" in data
        assert "host" in data
    finally:
        lock.release()
