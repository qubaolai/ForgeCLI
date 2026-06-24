"""/model 面板测试：恢复当前面板展示，同时拒绝参数式命令。"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.config.config_service import ConfigService
from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.llm.availability import EnvProviderAvailability
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.menu import Choice, Menu
from forgecli.domain.intents import SlashCommand
from forgecli.infrastructure.config import TomlConfigStore
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.interfaces.cli.commands.model_command import ModelsCommand
from forgecli.interfaces.cli.menus.model_menu import ModelsMenu


class _CapturingPresenter(MenuPresenter):
    def __init__(self) -> None:
        self.presented: Menu | None = None

    def present(self, menu: Menu) -> None:
        self.presented = menu


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


def _find(menu: Menu, label: str) -> Choice:
    return next(choice for choice in menu.choices if choice.label == label)


def _services(
    tmp_path: Path,
) -> tuple[ConfigService, LlmConfigService, _RecordingOutput]:
    config = ConfigService(
        TomlConfigStore(tmp_path / ".forge" / "config.toml"),
        TomlConfigStore(
            tmp_path / ".forge" / "projects" / "repo-deadbeef" / "forge.toml"
        ),
    )
    llm = LlmConfigService(TomlLlmConfigStore(tmp_path / ".forge" / "llm.toml"))
    output = _RecordingOutput()
    return config, llm, output


def test_model_menu_keeps_provider_rows_and_uses_configured_models(
    tmp_path: Path, monkeypatch
) -> None:
    config, llm, output = _services(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    llm.add_model("deepseek", "deepseek-chat", {"context_window": 65536})
    menu = ModelsMenu(llm, EnvProviderAvailability(), config, output)

    root = menu.root_menu()

    labels = [choice.label for choice in root.choices]
    assert labels == ["当前模型", "DeepSeek", "MiMo"]
    provider_menu = _find(root, "DeepSeek").submenu()
    assert [choice.label for choice in provider_menu.choices] == ["deepseek-chat"]
    assert _find(root, "MiMo").submenu is None


def test_model_menu_select_persists_default_model(tmp_path: Path, monkeypatch) -> None:
    config, llm, output = _services(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    llm.add_model("deepseek", "deepseek-chat", {})
    menu = ModelsMenu(llm, EnvProviderAvailability(), config, output)
    model = _find(_find(menu.root_menu(), "DeepSeek").submenu(), "deepseek-chat")

    model.on_select()

    assert str(config.effective().default_model) == "deepseek:deepseek-chat"
    assert output.lines == ["已切换默认模型为 deepseek:deepseek-chat。"]


def test_model_command_rejects_args_instead_of_presenting_menu(tmp_path: Path) -> None:
    config, llm, output = _services(tmp_path)
    presenter = _CapturingPresenter()
    command = ModelsCommand(config, llm, presenter, output)

    command.execute(
        SlashCommand(
            raw_text="/model use deepseek:deepseek-chat",
            command="model",
            args=("use", "deepseek:deepseek-chat"),
        )
    )

    assert presenter.presented is None
    assert output.lines == [
        "当前 /model 不支持参数；请直接输入 /model 打开模型选择面板。"
    ]
