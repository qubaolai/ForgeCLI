"""/config「用途模型覆盖」子菜单交互（ADR-0011 §19）。

模式同 test_llm_menu：直接驱动 Choice 的 submenu / on_select，断言呈现与落盘。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.gateway import RequestOrigin
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.overrides_service import ModelOverridesService
from forgecli.application.menu import Choice, Menu
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.infrastructure.llm.overrides_toml_store import TomlModelOverridesStore
from forgecli.interfaces.cli.menus.config_menu import ConfigMenu
from forgecli.interfaces.cli.menus.overrides_menu import OverridesMenu


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


def _find(menu: Menu, label: str) -> Choice:
    return next(c for c in menu.choices if c.label == label)


def _setup(
    tmp_path: Path,
) -> tuple[OverridesMenu, ModelOverridesService, _RecordingOutput]:
    llm_path = tmp_path / "llm.toml"
    llm_path.write_text(
        "[llm.providers.deepseek.models.deepseek-chat]\ncontext_window = 65536\n",
        encoding="utf-8",
    )
    llm = LlmConfigService(TomlLlmConfigStore(llm_path))
    overrides = ModelOverridesService(
        TomlModelOverridesStore(tmp_path / "forge.toml"), llm
    )
    output = _RecordingOutput()
    return OverridesMenu(overrides, llm, output), overrides, output


def test_root_menu_lists_all_origins(tmp_path: Path) -> None:
    menu, _, _ = _setup(tmp_path)
    labels = {c.label for c in menu.root_menu().choices}
    assert labels == {origin.value for origin in RequestOrigin}


def test_unset_origin_preview_falls_back_to_current_model(tmp_path: Path) -> None:
    menu, _, _ = _setup(tmp_path)
    title_row = _find(menu.root_menu(), "title")
    assert "当前模型" in title_row.preview()


def test_select_model_sets_override_and_persists(tmp_path: Path) -> None:
    menu, overrides, output = _setup(tmp_path)
    origin_menu = _find(menu.root_menu(), "title").submenu()
    _find(origin_menu, "deepseek:deepseek-chat").on_select()

    assert overrides.override_for(RequestOrigin.TITLE) == ModelRef(
        provider="deepseek", model="deepseek-chat"
    )
    assert output.lines and "已设置" in output.lines[-1]
    # preview 就地刷新（presenter 每次渲染重建）。
    assert "deepseek:deepseek-chat" in _find(menu.root_menu(), "title").preview()


def test_clear_override_falls_back(tmp_path: Path) -> None:
    menu, overrides, output = _setup(tmp_path)
    overrides.set_override(
        RequestOrigin.TITLE, ModelRef(provider="deepseek", model="deepseek-chat")
    )
    origin_menu = _find(menu.root_menu(), "title").submenu()
    _find(origin_menu, "清除覆盖（回落当前模型）").on_select()
    assert overrides.override_for(RequestOrigin.TITLE) is None
    assert output.lines and "已清除" in output.lines[-1]


def test_config_root_menu_contains_overrides_without_global_thinking_rows(
    tmp_path: Path,
) -> None:
    llm = LlmConfigService(TomlLlmConfigStore(tmp_path / "llm.toml"))
    overrides = ModelOverridesService(
        TomlModelOverridesStore(tmp_path / "forge.toml"), llm
    )
    from forgecli.application.config.config_service import ConfigService
    from forgecli.infrastructure.config.toml_store import TomlConfigStore

    config = ConfigService(
        TomlConfigStore(tmp_path / "config.toml"),
        TomlConfigStore(tmp_path / "forge.toml"),
    )
    output = _RecordingOutput()
    menu = ConfigMenu(llm, config, output, overrides_service=overrides)
    labels = [c.label for c in menu.root_menu().choices]
    assert "用途模型覆盖" in labels
    assert "思考(thinking)默认" not in labels
    assert "思考强度默认" not in labels


def test_config_menu_without_overrides_service_omits_row(tmp_path: Path) -> None:
    llm = LlmConfigService(TomlLlmConfigStore(tmp_path / "llm.toml"))
    from forgecli.application.config.config_service import ConfigService
    from forgecli.infrastructure.config.toml_store import TomlConfigStore

    config = ConfigService(TomlConfigStore(tmp_path / "config.toml"))
    menu = ConfigMenu(llm, config, _RecordingOutput())
    labels = [c.label for c in menu.root_menu().choices]
    assert "用途模型覆盖" not in labels
