"""ProviderSettingsSource / credential_refs 配置解析（ADR-0011 §5 / §7 / §16）。

覆盖：provider 默认（timeout/max_retries）、credential_refs 显式配置优先、
api_key_env 派生、keyless provider、未知 provider。
"""

from pathlib import Path

import pytest

from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigValidationError, UnknownProvider
from forgecli.infrastructure.llm.settings import LlmConfigProviderSettingsSource
from forgecli.infrastructure.llm.toml_store import TomlLlmConfigStore


def _service(tmp_path: Path, content: str = "") -> LlmConfigService:
    path = tmp_path / "llm.toml"
    if content:
        path.write_text(content, encoding="utf-8")
    return LlmConfigService(TomlLlmConfigStore(path))


def test_defaults_when_provider_not_configured(tmp_path: Path) -> None:
    source = LlmConfigProviderSettingsSource(_service(tmp_path))
    settings = source.settings_for("deepseek")
    assert settings.timeout_seconds == 60.0
    assert settings.max_retries == 2
    assert settings.credential_refs == ("DEEPSEEK_API_KEY",)


def test_keyless_provider_has_no_credential_refs(tmp_path: Path) -> None:
    source = LlmConfigProviderSettingsSource(_service(tmp_path))
    settings = source.settings_for("local")
    assert settings.credential_refs == ()


def test_explicit_credential_refs_take_precedence(tmp_path: Path) -> None:
    content = """
[llm.providers.deepseek]
credential_refs = ["KEY_MAIN", "KEY_BACKUP"]
"""
    source = LlmConfigProviderSettingsSource(_service(tmp_path, content))
    settings = source.settings_for("deepseek")
    assert settings.credential_refs == ("KEY_MAIN", "KEY_BACKUP")


def test_configured_timeout_and_retries_flow_through(tmp_path: Path) -> None:
    content = """
[llm.providers.deepseek]
timeout = 30
max_retries = 5
"""
    source = LlmConfigProviderSettingsSource(_service(tmp_path, content))
    settings = source.settings_for("deepseek")
    assert settings.timeout_seconds == 30.0
    assert settings.max_retries == 5


def test_api_key_env_derives_single_ref(tmp_path: Path) -> None:
    content = """
[llm.providers.openai]
api_key_env = "MY_OPENAI_KEY"
"""
    source = LlmConfigProviderSettingsSource(_service(tmp_path, content))
    settings = source.settings_for("openai")
    assert settings.credential_refs == ("MY_OPENAI_KEY",)


def test_unknown_provider_rejected(tmp_path: Path) -> None:
    source = LlmConfigProviderSettingsSource(_service(tmp_path))
    with pytest.raises(UnknownProvider):
        source.settings_for("nope")


def test_invalid_credential_refs_config_rejected(tmp_path: Path) -> None:
    content = """
[llm.providers.deepseek]
credential_refs = "NOT_A_LIST"
"""
    service = _service(tmp_path, content)
    with pytest.raises(ConfigValidationError):
        service.config()
