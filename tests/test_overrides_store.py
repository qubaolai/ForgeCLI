"""TomlModelOverridesStore / ModelOverridesService（ADR-0011 §16）。

覆盖：round-trip 保留 forge.toml 其余内容、设/清覆盖、类型化读取、
覆盖必须指向已声明模型、未知 origin / provider 拒绝。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigValidationError
from forgecli.application.llm.gateway import ModelBadRequestError, RequestOrigin
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.overrides_service import ModelOverridesService
from forgecli.infrastructure.llm.overrides_toml_store import TomlModelOverridesStore
from forgecli.infrastructure.llm.toml_store import TomlLlmConfigStore


def _forge_toml(tmp_path: Path) -> Path:
    return tmp_path / "forge.toml"


def _llm_service(tmp_path: Path, *, with_model: bool = True) -> LlmConfigService:
    path = tmp_path / "llm.toml"
    if with_model:
        path.write_text(
            "[llm.providers.deepseek.models.deepseek-chat]\ncontext_window = 65536\n",
            encoding="utf-8",
        )
    return LlmConfigService(TomlLlmConfigStore(path))


def _service(tmp_path: Path) -> ModelOverridesService:
    return ModelOverridesService(
        TomlModelOverridesStore(_forge_toml(tmp_path)), _llm_service(tmp_path)
    )


def test_empty_file_loads_empty_overrides(tmp_path: Path) -> None:
    assert _service(tmp_path).overrides() == {}


def test_set_and_load_override_round_trip(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ref = ModelRef(provider="deepseek", model="deepseek-chat")
    service.set_override(RequestOrigin.TITLE, ref)
    assert service.overrides() == {RequestOrigin.TITLE: ref}
    assert service.override_for(RequestOrigin.TITLE) == ref
    assert service.override_for(RequestOrigin.CHAT) is None


def test_set_override_preserves_other_forge_toml_content(tmp_path: Path) -> None:
    forge = _forge_toml(tmp_path)
    forge.write_text(
        '# 项目配置\n[model]\nprovider = "deepseek"\nname = "deepseek-chat"\n',
        encoding="utf-8",
    )
    _service(tmp_path).set_override(
        RequestOrigin.TITLE, ModelRef(provider="deepseek", model="deepseek-chat")
    )
    text = forge.read_text(encoding="utf-8")
    assert "# 项目配置" in text  # round-trip 保注释
    assert "[model]" in text
    assert "model_overrides" in text


def test_clear_override_removes_entry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ref = ModelRef(provider="deepseek", model="deepseek-chat")
    service.set_override(RequestOrigin.TITLE, ref)
    service.clear_override(RequestOrigin.TITLE)
    assert service.overrides() == {}
    assert "model_overrides" not in _forge_toml(tmp_path).read_text(encoding="utf-8")


def test_clear_missing_override_is_noop(tmp_path: Path) -> None:
    _service(tmp_path).clear_override(RequestOrigin.TITLE)  # 不抛错


def test_override_must_reference_declared_model(tmp_path: Path) -> None:
    service = ModelOverridesService(
        TomlModelOverridesStore(_forge_toml(tmp_path)),
        _llm_service(tmp_path, with_model=False),
    )
    with pytest.raises(ConfigValidationError):
        service.set_override(
            RequestOrigin.TITLE, ModelRef(provider="deepseek", model="nope")
        )


def test_unknown_origin_in_file_rejected_on_read(tmp_path: Path) -> None:
    _forge_toml(tmp_path).write_text(
        '[model_overrides.nonsense]\nprovider = "deepseek"\nmodel = "deepseek-chat"\n',
        encoding="utf-8",
    )
    with pytest.raises(ModelBadRequestError):
        _service(tmp_path).overrides()
