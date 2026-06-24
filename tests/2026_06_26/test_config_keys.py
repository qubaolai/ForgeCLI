"""2026-06-26：配置项按应用级 / 项目级路由。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.config import config_keys
from forgecli.application.config.config_keys import ConfigLevel
from forgecli.application.config.config_service import ConfigService
from forgecli.application.config.errors import ConfigValidationError
from forgecli.application.llm.model_ref import ModelRef
from forgecli.infrastructure.config import TomlConfigStore


def _service(tmp_path: Path) -> tuple[ConfigService, Path, Path]:
    config_toml = tmp_path / "config.toml"
    forge_toml = tmp_path / "projects" / "repo-deadbeef" / "forge.toml"
    return (
        ConfigService(TomlConfigStore(config_toml), TomlConfigStore(forge_toml)),
        config_toml,
        forge_toml,
    )


def test_every_key_declares_a_level() -> None:
    for key in config_keys.SCHEMA:
        assert isinstance(key.level, ConfigLevel)


def test_keys_for_partitions_schema() -> None:
    app = set(config_keys.keys_for(ConfigLevel.APP))
    project = set(config_keys.keys_for(ConfigLevel.PROJECT))

    assert app and project
    assert app.isdisjoint(project)
    assert app | project == set(config_keys.SCHEMA)
    assert {k.name for k in app} == {
        "logging.level",
        "output.theme",
        "telemetry.enabled",
    }
    assert {k.name for k in project} == {
        "model.name",
        "model.provider",
    }


def test_generic_set_routes_by_level(tmp_path: Path) -> None:
    service, config_toml, forge_toml = _service(tmp_path)

    service.set(config_keys.OUTPUT_THEME, "light")
    service.set(config_keys.LOGGING_LEVEL, "debug")
    service.set(config_keys.DEFAULT_MODEL_PROVIDER_KEY, "deepseek")
    service.set(config_keys.DEFAULT_MODEL_NAME_KEY, "deepseek-chat")

    app_text = config_toml.read_text(encoding="utf-8")
    project_text = forge_toml.read_text(encoding="utf-8")
    assert 'theme = "light"' in app_text
    assert 'level = "debug"' in app_text
    assert "[model]" not in app_text
    assert 'provider = "deepseek"' in project_text
    assert 'name = "deepseek-chat"' in project_text
    assert service.display(config_keys.OUTPUT_THEME) == "light"
    assert service.display(config_keys.LOGGING_LEVEL) == "debug"
    assert service.effective().default_model == ModelRef(
        provider="deepseek",
        model="deepseek-chat",
    )


def test_app_only_service_refuses_model_keys_but_allows_log_level(
    tmp_path: Path,
) -> None:
    config_toml = tmp_path / "config.toml"
    service = ConfigService(TomlConfigStore(config_toml))

    service.set(config_keys.LOGGING_LEVEL, "debug")
    assert service.display(config_keys.LOGGING_LEVEL) == "debug"

    for key in (
        config_keys.DEFAULT_MODEL_PROVIDER_KEY,
        config_keys.DEFAULT_MODEL_NAME_KEY,
    ):
        with pytest.raises(ConfigValidationError):
            service.set(key, "deepseek")
