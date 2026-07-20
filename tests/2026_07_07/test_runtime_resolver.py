"""ConfigBackedSelectionResolver：配置现读的动态解析（ADR-0011 §5 运行期装配）。

会话中 /model 换当前模型、/config 改覆盖后，下一次调用即生效（不重启进程）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.config import config_keys
from forgecli.application.config.config_service import ConfigService
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.gateway import (
    CurrentModelSelection,
    ModelBadRequestError,
    RequestOrigin,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.overrides_service import ModelOverridesService
from forgecli.application.llm.runtime_resolver import ConfigBackedSelectionResolver
from forgecli.infrastructure.config.toml_store import TomlConfigStore
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.infrastructure.llm.overrides_toml_store import TomlModelOverridesStore

_LLM_TOML = """
[llm.providers.deepseek.models.deepseek-chat]
context_window = 65536

[llm.providers.deepseek.models.deepseek-reasoner]
context_window = 65536
"""


def _resolver(
    tmp_path: Path,
) -> tuple[ConfigBackedSelectionResolver, ConfigService, ModelOverridesService]:
    llm_path = tmp_path / "llm.toml"
    llm_path.write_text(_LLM_TOML, encoding="utf-8")
    llm = LlmConfigService(TomlLlmConfigStore(llm_path))
    config = ConfigService(
        TomlConfigStore(tmp_path / "config.toml"),
        TomlConfigStore(tmp_path / "forge.toml"),
    )
    overrides = ModelOverridesService(
        TomlModelOverridesStore(tmp_path / "forge.toml"), llm
    )
    resolver = ConfigBackedSelectionResolver(
        config_service=config,
        llm_config_service=llm,
        overrides_loader=overrides.overrides,
    )
    return resolver, config, overrides


def _set_current(config: ConfigService, provider: str, model: str) -> None:
    config.set(config_keys.DEFAULT_MODEL_PROVIDER_KEY, provider)
    config.set(config_keys.DEFAULT_MODEL_NAME_KEY, model)


def test_unconfigured_current_model_gives_actionable_error(tmp_path: Path) -> None:
    resolver, _, _ = _resolver(tmp_path)
    with pytest.raises(ModelBadRequestError):
        resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.CHAT)


def test_current_model_read_fresh_each_resolve(tmp_path: Path) -> None:
    resolver, config, _ = _resolver(tmp_path)
    _set_current(config, "deepseek", "deepseek-chat")
    first = resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.CHAT)
    assert first.ref == ModelRef(provider="deepseek", model="deepseek-chat")

    _set_current(config, "deepseek", "deepseek-reasoner")  # 会话中 /model 切换
    second = resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.CHAT)
    assert second.ref == ModelRef(provider="deepseek", model="deepseek-reasoner")


def test_override_read_fresh_each_resolve(tmp_path: Path) -> None:
    resolver, config, overrides = _resolver(tmp_path)
    _set_current(config, "deepseek", "deepseek-chat")
    overrides.set_override(
        RequestOrigin.TITLE, ModelRef(provider="deepseek", model="deepseek-reasoner")
    )
    resolved = resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.TITLE)
    assert resolved.ref.model == "deepseek-reasoner"

    overrides.clear_override(RequestOrigin.TITLE)
    resolved = resolver.resolve(CurrentModelSelection(), origin=RequestOrigin.TITLE)
    assert resolved.ref.model == "deepseek-chat"  # 回落当前模型
