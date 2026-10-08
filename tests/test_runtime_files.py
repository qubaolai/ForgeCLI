"""临时文件回收不能触及持久状态、活跃运行或符号链接的目标."""

import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from forgecli.common import runtime_files
from forgecli.common.runtime_files import (
    RETENTION_SECONDS,
    RuntimeFiles,
    cleanup_stale_artifacts,
)
from forgecli.runtime import logging_wiring
from forgecli.settings import paths
from test_web_search import stack as _stack

stack = _stack


def age(path):
    old = time.time() - RETENTION_SECONDS - 10
    os.utime(path, (old, old))


def test_cleanup_expires_artifacts_but_preserves_active_and_recent(tmp_path):
    root = tmp_path / "runtime"
    inactive = RuntimeFiles(root)
    logs = inactive.path / "logs"
    logs.mkdir()
    (logs / "forge.log").write_text("discardable")
    inactive.close()
    age(inactive.path)
    active = RuntimeFiles(root)
    # 启动时回收旧日志, 但不删除整个运行容器.
    assert inactive.path.exists() and not logs.exists()
    active_logs = active.path / "logs"
    active_logs.mkdir()
    (active_logs / "forge.log").write_text("active")
    age(active.path)
    recent = RuntimeFiles(root)
    recent_logs = recent.path / "logs"
    recent_logs.mkdir()
    (recent_logs / "forge.log").write_text("recent")
    try:
        cleanup_stale_artifacts(root)
        assert (active_logs / "forge.log").read_text() == "active"
        assert (recent_logs / "forge.log").read_text() == "recent"
    finally:
        active.close()
        recent.close()
    cleanup_stale_artifacts(root)
    # close 刷新保留期, 不会立刻清理刚结束的长时间运行的日志.
    assert (active_logs / "forge.log").read_text() == "active"


def test_cleanup_only_removes_allowlisted_artifacts_and_keeps_history(tmp_path):
    root = tmp_path / "runtime"
    runtime = RuntimeFiles(root)
    temporary_paths = [
        "logs",
        "artifacts",
        "sandbox-one",
        "projects/p/file-history",
        "projects/p/sessions/s/tool-results",
        "projects/p/sessions/s/diagnostics",
        "projects/p/sessions/s/agents",
    ]
    for relative in temporary_paths:
        directory = runtime.path / relative
        directory.mkdir(parents=True)
        (directory / "output.txt").write_text("temporary")
    persistent_paths = [
        "notes.txt",
        "projects/p/memory.json",
        "projects/p/source/main.py",
        "projects/p/sessions/s/state.json",
        "projects/p/sessions/s/events.jsonl",
        "projects/p/sessions/s/runs.jsonl",
        "projects/p/sessions/s/plans/plan.md",
        "projects/p/sessions/s/unknown/data.txt",
        "other/logs/user.log",
    ]
    for relative in persistent_paths:
        path = runtime.path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep")
    runtime.close()
    age(runtime.path)
    cleanup_stale_artifacts(root)
    assert all(not (runtime.path / path).exists() for path in temporary_paths)
    assert all((runtime.path / path).read_text() == "keep" for path in persistent_paths)
    assert (runtime.path / "projects/p/sessions/s").is_dir()
    assert (runtime.path / ".forge-runtime").is_file()


@pytest.mark.parametrize(
    "relative",
    [
        "projects",
        "projects/p",
        "projects/p/sessions",
        "projects/p/sessions/s",
        "projects/p/sessions/s/tool-results",
        "logs",
        "sandbox-one",
    ],
)
def test_cleanup_does_not_follow_symlinks_at_any_level(tmp_path, relative):
    root = tmp_path / "runtime"
    runtime = RuntimeFiles(root)
    outside = tmp_path / "history"
    outside.mkdir()
    # 包括每层遍历之后可能匹配白名单的真实目录.
    for child in [
        "logs",
        "tool-results",
        "s/tool-results",
        "sessions/s/tool-results",
        "p/sessions/s/tool-results",
    ]:
        directory = outside / child
        directory.mkdir(parents=True)
        (directory / "keep.txt").write_text("keep")
    before = sorted(path.relative_to(outside) for path in outside.rglob("*"))
    link = runtime.path / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        runtime.close()
        pytest.skip("symlinks unavailable")
    runtime.close()
    age(runtime.path)
    cleanup_stale_artifacts(root)
    assert link.is_symlink()
    assert sorted(path.relative_to(outside) for path in outside.rglob("*")) == before
    assert all(path.read_text() == "keep" for path in outside.rglob("keep.txt"))


def test_cleanup_ignores_unmarked_directory_and_symlink(tmp_path):
    root = tmp_path / "runtime"
    runtime = RuntimeFiles(root)
    unknown = root / "run-user-data"
    unknown.mkdir()
    age(unknown)
    outside = tmp_path / "important"
    outside.mkdir()
    (outside / ".forge-runtime").write_text("1")
    age(outside)
    link = root / "run-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        runtime.close()
        pytest.skip("symlinks unavailable")
    try:
        cleanup_stale_artifacts(root)
        assert outside.exists() and link.is_symlink() and unknown.exists()
        with pytest.raises(OSError):
            RuntimeFiles(link)
    finally:
        runtime.close()


def test_system_temp_root_is_private_and_shared_within_process(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_files.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(runtime_files, "_runtime", None)
    path = runtime_files.runtime_dir()
    try:
        assert path.is_relative_to(tmp_path)
        assert runtime_files.runtime_dir() == path
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o700
            assert path.parent.stat().st_mode & 0o777 == 0o700
    finally:
        runtime_files._runtime.close()


def test_persistent_paths_and_temporary_paths_are_separate(tmp_path, monkeypatch):
    home = tmp_path / "config"
    run = tmp_path / "temp" / "run-one"
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(home))
    monkeypatch.setattr(paths, "runtime_dir", lambda: run)
    assert paths.artifacts_dir() == run / "artifacts"
    assert paths.session_runtime_dir("p", "s") == run / "projects/p/sessions/s"
    assert paths.config_file("config.json") == home / "config.json"
    assert paths.recovery_dir() == home / "state/recovery"
    assert paths.plans_dir("p", "s") == home / "projects/p/sessions/s/plans"
    assert paths.user_memory_file() == home / "memory.json"


@pytest.mark.parametrize("override", ["", "custom-logs"])
def test_default_logging_is_temporary_but_explicit_override_is_honored(
    tmp_path, monkeypatch, override
):
    run = tmp_path / "runtime"
    configured = str(tmp_path / override) if override else ""
    monkeypatch.setattr(logging_wiring, "runtime_dir", lambda: run)
    monkeypatch.setattr(
        logging_wiring,
        "startup_config",
        lambda: SimpleNamespace(
            logging_directory=configured,
            logging_level="info",
            logging_console=False,
            logging_max_value_chars="",
            logging_include_http=False,
        ),
    )
    configure = Mock()
    monkeypatch.setattr(logging_wiring, "configure_logging", configure)
    logging_wiring.start_observability()
    assert configure.call_args.kwargs["directory"] == (
        tmp_path / override if override else run / "logs"
    )


def test_tool_stack_uses_temporary_outputs_and_backups(stack, tmp_path):
    expected = tmp_path / "runtime" / "projects" / "workspace"
    assert stack.file_history.backup_dir == expected / "file-history"
    assert stack.runner.tool_results_dir() == str(
        expected / "sessions/session/tool-results"
    )
    assert str(expected / "sessions") in stack.runner.internal_paths
