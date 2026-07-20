"""thinking 跟随具体模型配置（ADR-0011 §3.5 / §16）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.config import config_keys
from forgecli.application.config.config_service import ConfigService
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigValidationError
from forgecli.application.llm.gateway import ThinkingEffort, ThinkingMode
from forgecli.application.llm.model_ref import ModelRef
from forgecli.infrastructure.config.toml_store import TomlConfigStore
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.interfaces.cli.bootstrap import _prompt_runtime_status


def _config_service(tmp_path: Path) -> ConfigService:
    return ConfigService(
        TomlConfigStore(tmp_path / "config.toml"),
        TomlConfigStore(tmp_path / "forge.toml"),
    )


def _llm_service(tmp_path: Path) -> LlmConfigService:
    return LlmConfigService(TomlLlmConfigStore(tmp_path / "llm.toml"))


def test_thinking_is_not_a_generic_app_or_project_key() -> None:
    assert config_keys.is_known("thinking.mode") is False
    assert config_keys.is_known("thinking.effort") is False


def test_new_model_gets_explicit_thinking_defaults(tmp_path: Path) -> None:
    llm = _llm_service(tmp_path)
    llm.add_model("deepseek", "deepseek-chat", {})

    params = llm.config().model("deepseek", "deepseek-chat").params
    assert params.thinking_mode is ThinkingMode.AUTO
    assert params.thinking_effort is ThinkingEffort.NONE
    assert (tmp_path / "llm.toml").exists()
    assert not (tmp_path / "config.toml").exists()
    assert not (tmp_path / "forge.toml").exists()


def test_model_thinking_fields_round_trip_and_feed_catalog(tmp_path: Path) -> None:
    llm = _llm_service(tmp_path)
    llm.add_model(
        "deepseek",
        "deepseek-reasoner",
        {"extra": {"supports_thinking": True}},
    )
    llm.set_model_field("deepseek", "deepseek-reasoner", "thinking_mode", "on")
    llm.set_model_field("deepseek", "deepseek-reasoner", "thinking_effort", "high")

    entry = build_catalog(llm.config()).get(
        ModelRef(provider="deepseek", model="deepseek-reasoner")
    )
    assert entry.thinking_mode is ThinkingMode.ON
    assert entry.thinking_effort is ThinkingEffort.HIGH


@pytest.mark.parametrize(
    ("field", "value"),
    (("thinking_mode", "maybe"), ("thinking_effort", "extreme")),
)
def test_invalid_model_thinking_choice_rejected(
    tmp_path: Path, field: str, value: str
) -> None:
    llm = _llm_service(tmp_path)
    llm.add_model("deepseek", "deepseek-chat", {})
    with pytest.raises(ConfigValidationError):
        llm.set_model_field("deepseek", "deepseek-chat", field, value)


def test_prompt_status_combines_project_model_and_its_thinking(tmp_path: Path) -> None:
    config = _config_service(tmp_path)
    llm = _llm_service(tmp_path)
    config.set(config_keys.DEFAULT_MODEL_PROVIDER_KEY, "deepseek")
    config.set(config_keys.DEFAULT_MODEL_NAME_KEY, "deepseek-reasoner")
    llm.add_model(
        "deepseek",
        "deepseek-reasoner",
        {
            "thinking_mode": "auto",
            "thinking_effort": "medium",
            "extra": {"supports_thinking": True},
        },
    )

    assert _prompt_runtime_status(config, llm) == (
        "模型 deepseek:deepseek-reasoner · thinking auto/medium"
    )

    llm.add_model(
        "deepseek",
        "deepseek-chat",
        {"thinking_mode": "off", "thinking_effort": "none"},
    )
    config.set(config_keys.DEFAULT_MODEL_NAME_KEY, "deepseek-chat")
    assert _prompt_runtime_status(config, llm) == (
        "模型 deepseek:deepseek-chat · thinking off/none"
    )
