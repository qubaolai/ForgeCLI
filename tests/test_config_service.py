"""ConfigService + TOML 持久化的验收测试（2026-06-24）。

覆盖 roadmap 验收点：默认可靠、未配置不建文件、首次修改才写文件、更新经 service、
可配置面封闭且无凭证字段、未知字段忽略、TOML 语法错误友好处理、写入可重复可验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.config import keys
from forgecli.application.config.default import default_config
from forgecli.application.config.errors import (
    ConfigReadError,
    ConfigValidationError,
    UnknownConfigKey,
)
from forgecli.application.config.model import EffectiveConfig
from forgecli.application.config.service import FileConfigService
from forgecli.infrastructure.config import TomlConfigStore


def _service(tmp_path: Path) -> tuple[FileConfigService, Path]:
    config_path = tmp_path / ".forge" / "config.toml"
    return FileConfigService(TomlConfigStore(config_path)), config_path


# ---- 默认值 / 未配置 ----


def test_effective_returns_defaults_when_unconfigured(tmp_path: Path) -> None:
    service, config_path = _service(tmp_path)

    effective = service.effective()

    assert effective == default_config()
    assert effective.telemetry_enabled is False
    assert effective.output_theme == "dark"
    assert effective.log_level == "info"
    # 未配置不应创建文件，甚至不创建 .forge 目录。
    assert not config_path.exists()
    assert not config_path.parent.exists()


def test_get_returns_none_for_unset_key(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    assert service.get(keys.OUTPUT_THEME) is None


# ---- 首次修改才落盘 ----


def test_set_creates_config_file_on_first_change(tmp_path: Path) -> None:
    service, config_path = _service(tmp_path)

    service.set(keys.OUTPUT_THEME, "light")

    assert config_path.exists()
    assert service.get(keys.OUTPUT_THEME) == "light"
    assert service.effective().output_theme == "light"


def test_set_persists_across_service_instances(tmp_path: Path) -> None:
    service, config_path = _service(tmp_path)
    service.set(keys.LOG_LEVEL, "debug")

    reopened = FileConfigService(TomlConfigStore(config_path))

    assert reopened.effective().log_level == "debug"
    assert reopened.get(keys.LOG_LEVEL) == "debug"


# ---- 封闭可配置面 / 敏感字段 ----


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    with pytest.raises(UnknownConfigKey):
        service.set("api.key", "secret")
    with pytest.raises(UnknownConfigKey):
        service.get("api.key")


def test_schema_has_no_credential_fields() -> None:
    # 凭证保护 = 白名单里压根没有凭证字段；这是防止有人往可配置面塞凭证的回归守卫。
    suspicious = ("key", "secret", "token", "password", "credential")
    for key in keys.SCHEMA:
        assert not any(word in key.name.lower() for word in suspicious)


# ---- 校验 / 归一化 ----


def test_invalid_choice_is_rejected(tmp_path: Path) -> None:
    service, config_path = _service(tmp_path)

    with pytest.raises(ConfigValidationError):
        service.set(keys.OUTPUT_THEME, "rainbow")
    # 非法写入不应落盘。
    assert not config_path.exists()


def test_bool_value_is_normalized(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    service.set(keys.TELEMETRY_ENABLED, "YES")

    assert service.get(keys.TELEMETRY_ENABLED) == "true"
    assert service.effective().telemetry_enabled is True


def test_relative_path_is_resolved_to_absolute(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    service.set(keys.WORKSPACE_DIR, "sub/work")

    stored = service.get(keys.WORKSPACE_DIR)
    assert stored is not None
    assert Path(stored).is_absolute()


# ---- 未知字段忽略 ----


def test_unknown_fields_in_file_are_ignored(tmp_path: Path) -> None:
    config_path = tmp_path / ".forge" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'output.theme = "light"\n'
        "[experimental]\n"
        'flux = "on"\n'
        "[telemetry]\n"
        "enabled = true\n",
        encoding="utf-8",
    )
    service = FileConfigService(TomlConfigStore(config_path))

    effective = service.effective()

    # 已知字段生效，未知 experimental.flux 不进入有效配置、也不报错。
    assert effective.output_theme == "light"
    assert effective.telemetry_enabled is True
    assert isinstance(effective, EffectiveConfig)
    assert not hasattr(effective, "flux")


# ---- TOML 语法错误友好处理 ----


def test_broken_toml_raises_friendly_read_error(tmp_path: Path) -> None:
    config_path = tmp_path / ".forge" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("this is = not = valid toml ===", encoding="utf-8")
    service = FileConfigService(TomlConfigStore(config_path))

    with pytest.raises(ConfigReadError) as excinfo:
        service.effective()

    # 面向用户的 message，不暴露 traceback 细节。
    assert str(config_path) in excinfo.value.message
    assert "语法错误" in excinfo.value.message


# ---- 写入可重复 / 可验证 ----


def test_writes_are_deterministic_and_repeatable(tmp_path: Path) -> None:
    service, config_path = _service(tmp_path)

    service.set(keys.OUTPUT_THEME, "light")
    service.set(keys.LOG_LEVEL, "warn")
    first = config_path.read_text(encoding="utf-8")

    # 用同样的值重写，字节应完全一致（幂等、可 diff）。
    service.set(keys.OUTPUT_THEME, "light")
    second = config_path.read_text(encoding="utf-8")

    assert first == second
    # 文件可被重新解析，值保持。
    assert service.effective().output_theme == "light"
    assert service.effective().log_level == "warn"


def test_set_preserves_other_existing_overrides(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    service.set(keys.OUTPUT_THEME, "light")
    service.set(keys.LOG_LEVEL, "debug")

    effective = service.effective()
    assert effective.output_theme == "light"
    assert effective.log_level == "debug"
