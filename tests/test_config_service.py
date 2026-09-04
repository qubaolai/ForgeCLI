from __future__ import annotations

from pathlib import Path

from forgecli.application.config.config_service import ConfigService
from forgecli.domain.config import config_keys
from forgecli.infrastructure.config.json_store import JsonConfigStore


def _service(tmp_path: Path) -> ConfigService:
    return ConfigService(
        JsonConfigStore(tmp_path / "config.json"),
        JsonConfigStore(tmp_path / "forge.json"),
    )


def test_value_view_distinguishes_default_from_user_override(tmp_path: Path) -> None:
    service = _service(tmp_path)
    initial = {view.key.name: view for view in service.value_views()}
    assert initial[config_keys.OUTPUT_THEME].value == "dark"
    assert not initial[config_keys.OUTPUT_THEME].overridden

    service.set(config_keys.OUTPUT_THEME, "light")
    changed = {view.key.name: view for view in service.value_views()}
    assert changed[config_keys.OUTPUT_THEME].value == "light"
    assert changed[config_keys.OUTPUT_THEME].overridden


def test_unset_removes_override_and_restores_schema_default(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.set(config_keys.OUTPUT_THEME, "light")
    service.set(config_keys.LOGGING_LEVEL, "debug")
    service.unset(config_keys.OUTPUT_THEME)

    assert service.get(config_keys.OUTPUT_THEME) is None
    assert service.display(config_keys.OUTPUT_THEME) == "dark"
    assert service.get(config_keys.LOGGING_LEVEL) == "debug"


def test_declared_blank_values_match_their_help_text(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.set(config_keys.LOGGING_DIRECTORY, "")
    service.set(config_keys.LOGGING_MAX_VALUE_CHARS, "")

    assert service.get(config_keys.LOGGING_DIRECTORY) == ""
    assert service.get(config_keys.LOGGING_MAX_VALUE_CHARS) == ""
