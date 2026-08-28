"""每个配置项都要有中文名 (2026-08-28).

配置面板原先直接显示 dotted key: `telemetry.enabled`, `execution.toolchain_dirs`.
那是给代码看的名字 —— 用户看到 `logging.max_value_chars` 猜不出它管什么, 也就不会去动它.

名字和说明住在 SCHEMA 上而不是各个界面里: CLI 菜单与 Web 设置页各写一份的话, 同一个开关
在两处会叫不同的名字, 而两边都不会因此报错.
"""

from __future__ import annotations

import pytest

from forgecli.domain.config import config_keys
from forgecli.domain.config.errors import ConfigValidationError


def test_every_key_has_a_chinese_label() -> None:
    missing = [key.name for key in config_keys.SCHEMA if not key.label]
    assert missing == []


def test_every_key_label_is_actually_chinese() -> None:
    """挡住"顺手填个英文占位"这种半吊子状态."""
    for key in config_keys.SCHEMA:
        assert any("一" <= char <= "鿿" for char in key.label), key.name


def test_the_title_falls_back_to_the_key_name() -> None:
    """没写标签时显示键名, 而不是一行空白."""
    bare = config_keys.ConfigKey(
        "a.b", config_keys.ConfigLevel.APP, config_keys.ValueKind.TEXT
    )
    assert bare.title == "a.b"


def test_switches_carry_an_explanation() -> None:
    """开关类配置项必须有一句说明: 用户看到"运行指标采集"仍然不知道关掉会失去什么."""
    for key in config_keys.SCHEMA:
        if key.kind in (config_keys.ValueKind.BOOL, config_keys.ValueKind.CHOICE):
            assert key.help, key.name


# ---- 整数类配置项 ----


def test_int_values_are_normalised() -> None:
    key = config_keys.require_known(config_keys.LOGGING_MAX_VALUE_CHARS)
    assert key.kind is config_keys.ValueKind.INT
    assert key.validate(" 0 ") == "0"
    assert key.validate("2048") == "2048"


@pytest.mark.parametrize("bad", ["很多", "-1", "1.5"])
def test_int_values_reject_junk(bad: str) -> None:
    key = config_keys.require_known(config_keys.LOGGING_MAX_VALUE_CHARS)
    with pytest.raises(ConfigValidationError):
        key.validate(bad)


# ---- 日志开关全部可配 ----


def test_every_logging_switch_is_a_config_key() -> None:
    """这几项原先是 FORGE_LOG_* 环境变量: 在设置面板里看不见, 也改不了."""
    names = {key.name for key in config_keys.SCHEMA}
    assert {
        config_keys.LOGGING_LEVEL,
        config_keys.LOGGING_CONSOLE,
        config_keys.LOGGING_DIRECTORY,
        config_keys.LOGGING_MAX_VALUE_CHARS,
        config_keys.LOGGING_INCLUDE_HTTP,
    } <= names


def test_logging_switches_are_application_level() -> None:
    """日志装配发生在项目解析之前, 所以它不可能是项目级配置."""
    for name in (
        config_keys.LOGGING_LEVEL,
        config_keys.LOGGING_CONSOLE,
        config_keys.LOGGING_DIRECTORY,
        config_keys.LOGGING_MAX_VALUE_CHARS,
        config_keys.LOGGING_INCLUDE_HTTP,
    ):
        assert config_keys.require_known(name).level is config_keys.ConfigLevel.APP
