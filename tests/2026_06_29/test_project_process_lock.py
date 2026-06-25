"""2026-06-29：项目级排他锁。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from forgecli.infrastructure.project.process_lock import ProcessLock, ProjectLockedError


def test_process_lock_rejects_second_holder_for_same_project(tmp_path: Path) -> None:
    lock_path = tmp_path / "projects" / "repo-test" / "forge.lock"
    first = ProcessLock(lock_path)
    second = ProcessLock(lock_path)

    first.acquire()
    try:
        with pytest.raises(ProjectLockedError) as exc_info:
            second.acquire()

        assert "Forge 已在该项目运行" in exc_info.value.message
        assert exc_info.value.pid in (None, os.getpid())
    finally:
        first.release()
        second.release()


def test_process_lock_can_be_acquired_after_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "projects" / "repo-test" / "forge.lock"

    first = ProcessLock(lock_path)
    first.acquire()
    first.release()

    second = ProcessLock(lock_path)
    try:
        second.acquire()
    finally:
        second.release()
