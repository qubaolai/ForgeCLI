"""日志装配 (ADR-0035 决策 3): 写到哪, 写多细, 重复装配会不会写两份."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from forgecli.shared.observability.configure import (
    configure_logging,
    logging_status,
)
from forgecli.shared.observability.log import ROOT_LOGGER_NAME, get_log


def test_writes_a_file_named_by_pid(tmp_path: Path) -> None:
    status = configure_logging(directory=tmp_path, level="info")

    assert status.file is not None
    assert status.file.parent == tmp_path
    get_log("t").info("turn.received", text="你好")
    written = status.file.read_text("utf-8")
    assert "turn.received" in written
    assert "text=你好" in written


def test_the_configured_level_is_the_only_source(tmp_path: Path) -> None:
    """级别只从配置来.

    原先还有一个 FORGE_LOG_LEVEL 环境变量压在它上面. 那个变量在设置面板里看不见也改不了,
    于是"日志级别显示 info, 实际按 debug 在写"这种状态没有任何界面能解释.
    """
    status = configure_logging(directory=tmp_path, level="debug")
    assert status.level == "debug"
    assert logging.getLogger(ROOT_LOGGER_NAME).level == logging.DEBUG

    assert configure_logging(directory=tmp_path, level="warn").level == "warning"


def test_an_unreadable_level_falls_back_to_info(tmp_path: Path) -> None:
    """装配发生在配置校验之前, 所以写坏的值只能退回默认, 不能抛."""
    assert configure_logging(directory=tmp_path, level="很详细").level == "info"


def test_console_is_off_unless_configured(tmp_path: Path) -> None:
    assert configure_logging(directory=tmp_path, level="info").console is False
    assert configure_logging(directory=tmp_path, level="info", console=True).console


def test_reconfigure_does_not_write_twice(tmp_path: Path) -> None:
    first = configure_logging(directory=tmp_path, level="info")
    second = configure_logging(directory=tmp_path / "again", level="info")

    assert second.file is not None
    get_log("t").info("forge.start")
    assert first.file is not None
    # 旧文件不再收新记录: 重复装配拆掉的是上一次装的 handler, 不是所有 handler.
    assert "forge.start" not in first.file.read_text("utf-8")
    assert "forge.start" in second.file.read_text("utf-8")


def test_unwritable_directory_does_not_stop_forge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写不了日志是个遗憾, 让 forge 起不来是个故障."""

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", _refuse)
    status = configure_logging(directory=tmp_path / "nope", level="info")

    assert status.configured is True
    assert status.file is None


def test_max_value_chars_reaches_the_renderer(tmp_path: Path) -> None:
    status = configure_logging(directory=tmp_path, level="debug", max_value_chars="0")

    assert status.max_value_chars == 0  # 0 = 不截断, 给"这次一定要看到全部"用
    assert logging_status().max_value_chars == 0


def test_a_blank_max_value_keeps_the_built_in_ceiling(tmp_path: Path) -> None:
    """留空和填 0 是两件事: 前者"不改", 后者"不截断"."""
    assert configure_logging(directory=tmp_path, level="info").max_value_chars > 0


def test_an_unreadable_max_value_keeps_the_built_in_ceiling(tmp_path: Path) -> None:
    status = configure_logging(directory=tmp_path, level="info", max_value_chars="很多")
    assert status.max_value_chars > 0


def test_latest_symlink_points_at_this_run(tmp_path: Path) -> None:
    status = configure_logging(directory=tmp_path, level="info")

    link = tmp_path / "forge-latest.log"
    assert status.file is not None
    if link.is_symlink():  # Windows 上建不了符号链接是常态, 不算失败
        assert link.resolve() == status.file.resolve()
