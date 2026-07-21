"""thinking 跟随具体模型配置（ADR-0011 §3.5 / §16）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.config import config_keys
from forgecli.application.config.config_service import ConfigService
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigValidationError
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.thinking import ThinkingEffortName, ThinkingMode
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
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


def test_new_model_does_not_invent_thinking_defaults(tmp_path: Path) -> None:
    llm = _llm_service(tmp_path)
    llm.add_model("deepseek", "deepseek-chat", {})

    params = llm.config().model("deepseek", "deepseek-chat").params
    assert params.thinking_mode is None
    assert params.thinking_effort is None
    assert (tmp_path / "llm.toml").exists()
    assert not (tmp_path / "config.toml").exists()
    assert not (tmp_path / "forge.toml").exists()


def test_model_thinking_fields_round_trip_and_feed_catalog(tmp_path: Path) -> None:
    llm = _llm_service(tmp_path)
    llm.add_model(
        "deepseek",
        "deepseek-reasoner",
        {
            "thinking_efforts": ["high", "max"],
            "thinking_default_effort": "high",
        },
    )
    changed = llm.update_model_thinking(
        "deepseek",
        "deepseek-reasoner",
        mode=ThinkingMode.ON,
        effort=ThinkingEffortName("max"),
    )

    entry = build_catalog(llm.config()).get(
        ModelRef(provider="deepseek", model="deepseek-reasoner")
    )
    assert changed is True
    assert entry.thinking_mode is ThinkingMode.ON
    assert entry.effective_thinking_effort == ThinkingEffortName("max")


def test_enabling_mode_needs_no_separate_capability_field(tmp_path: Path) -> None:
    llm = _llm_service(tmp_path)
    llm.add_model("deepseek", "custom-reasoner", {})

    assert llm.update_model_thinking(
        "deepseek", "custom-reasoner", mode=ThinkingMode.ON
    )

    params = llm.config().model("deepseek", "custom-reasoner").params
    assert params.thinking_mode is ThinkingMode.ON


def test_model_rejects_effort_outside_its_capability_list(tmp_path: Path) -> None:
    llm = _llm_service(tmp_path)
    llm.add_model(
        "deepseek",
        "deepseek-reasoner",
        {
            "thinking_efforts": ["high", "max"],
            "thinking_default_effort": "high",
        },
    )
    with pytest.raises(ConfigValidationError):
        llm.update_model_thinking(
            "deepseek",
            "deepseek-reasoner",
            effort=ThinkingEffortName("xhigh"),
        )


def test_prompt_status_combines_project_model_and_its_thinking(tmp_path: Path) -> None:
    config = _config_service(tmp_path)
    llm = _llm_service(tmp_path)
    config.set(config_keys.DEFAULT_MODEL_PROVIDER_KEY, "deepseek")
    config.set(config_keys.DEFAULT_MODEL_NAME_KEY, "deepseek-reasoner")
    llm.add_model(
        "deepseek",
        "deepseek-reasoner",
        {
            "thinking_mode": "on",
            "thinking_effort": "medium",
            "thinking_efforts": ["low", "medium", "high"],
            "thinking_default_effort": "medium",
        },
    )

    assert _prompt_runtime_status(config, llm) == (
        "模型 deepseek:deepseek-reasoner · thinking on/medium"
    )

    llm.add_model(
        "deepseek",
        "deepseek-chat",
        {"thinking_mode": "off"},
    )
    config.set(config_keys.DEFAULT_MODEL_NAME_KEY, "deepseek-chat")
    assert (
        _prompt_runtime_status(config, llm)
        == "模型 deepseek:deepseek-chat · thinking off"
    )


def test_prompt_status_reads_process_thinking_override(tmp_path: Path) -> None:
    config = _config_service(tmp_path)
    llm = _llm_service(tmp_path)
    config.set(config_keys.DEFAULT_MODEL_PROVIDER_KEY, "deepseek")
    config.set(config_keys.DEFAULT_MODEL_NAME_KEY, "deepseek-chat")
    llm.add_model(
        "deepseek",
        "deepseek-chat",
        {"thinking_mode": "off", "thinking_efforts": ["high"]},
    )
    ref = ModelRef("deepseek", "deepseek-chat")
    state = ThinkingRuntimeState()
    entry = build_catalog(llm.config()).get(ref)
    assert state.update(
        ref, entry, mode=ThinkingMode.ON, effort=ThinkingEffortName("high")
    )

    assert _prompt_runtime_status(config, llm, state) == (
        "模型 deepseek:deepseek-chat · thinking on/high"
    )
