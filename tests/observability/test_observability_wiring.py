"""启动时按配置装日志与指标 (2026-08-28).

这几个开关原先是 FORGE_LOG_* 环境变量与一个从没有人读过的 `telemetry.enabled`.
环境变量在设置面板里看不见也改不了; 而一个没有消费者的配置项, 改它等于什么都没做 ——
两种情况用户都无从判断开关到底生效没有.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from forgecli.domain.config import config_keys
from forgecli.interfaces.runtime.logging_wiring import start_observability
from forgecli.shared.observability.configure import reset_logging
from forgecli.shared.observability.metrics import METRICS


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "forge-home"
    home.mkdir()
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(home))
    try:
        yield home
    finally:
        reset_logging()
        METRICS.set_enabled(False)


def _write_config(home: Path, values: dict[str, str]) -> None:
    (home / "config.json").write_text(
        json.dumps(values, ensure_ascii=False), encoding="utf-8"
    )


def test_logs_land_under_forge_home_by_default(_isolated_home: Path) -> None:
    status = start_observability()
    assert status.file is not None
    assert status.file.parent == _isolated_home / "logs"


def test_the_configured_level_is_applied(_isolated_home: Path) -> None:
    _write_config(_isolated_home, {config_keys.LOGGING_LEVEL: "debug"})
    assert start_observability().level == "debug"


def test_the_configured_directory_is_applied(
    _isolated_home: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "somewhere-else"
    _write_config(_isolated_home, {config_keys.LOGGING_DIRECTORY: str(elsewhere)})

    status = start_observability()

    assert status.file is not None
    assert status.file.parent == elsewhere


def test_console_output_follows_the_config(_isolated_home: Path) -> None:
    assert start_observability().console is False
    _write_config(_isolated_home, {config_keys.LOGGING_CONSOLE: "true"})
    assert start_observability().console is True


def test_max_value_chars_follows_the_config(_isolated_home: Path) -> None:
    _write_config(_isolated_home, {config_keys.LOGGING_MAX_VALUE_CHARS: "0"})
    assert start_observability().max_value_chars == 0


def test_metrics_stay_off_until_telemetry_is_enabled(_isolated_home: Path) -> None:
    """`telemetry.enabled` 原先没有任何消费者: 打开或关掉它, agent 的行为一模一样."""
    start_observability()
    assert METRICS.enabled is False

    _write_config(_isolated_home, {config_keys.TELEMETRY_ENABLED: "true"})
    start_observability()
    assert METRICS.enabled is True


def test_disabled_telemetry_makes_the_recording_points_no_ops(
    _isolated_home: Path,
) -> None:
    start_observability()
    METRICS.count("tool.execute")
    METRICS.observe("tool.execute", 1.0)

    assert METRICS.snapshot() == {"counters": {}, "durations": {}}


def test_a_broken_config_still_lets_forge_start(_isolated_home: Path) -> None:
    """连日志都还没装上的时候报错, 用户手里什么线索都没有."""
    (_isolated_home / "config.json").write_text("{ 不是 JSON", encoding="utf-8")

    status = start_observability()

    assert status.configured is True
    assert status.level == "info"
