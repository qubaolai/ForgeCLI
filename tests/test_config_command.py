"""/config 交互入口测试：验证菜单只做呈现、所有改动都经 ConfigService 落盘，
并验证配置读取错误被翻成友好提示而非 traceback。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.config import config_keys
from forgecli.application.config.config_service import FileConfigService
from forgecli.application.config.config_store import ConfigStore
from forgecli.application.config.errors import ConfigReadError
from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.llm.config.llm_config_service import FileLlmConfigService
from forgecli.application.menu import Choice, Menu
from forgecli.domain.intents import SlashCommand
from forgecli.infrastructure.config import TomlConfigStore
from forgecli.infrastructure.llm.config import TomlLlmConfigStore
from forgecli.interfaces.cli.commands.config_command import ConfigCommand


class _CapturingPresenter(MenuPresenter):
    """记录被展示的菜单，不做真实终端驱动。"""

    def __init__(self) -> None:
        self.presented: Menu | None = None

    def present(self, menu: Menu) -> None:
        self.presented = menu


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


class _BrokenStore(ConfigStore):
    def load(self) -> dict[str, str]:
        raise ConfigReadError("配置文件存在语法错误，无法读取：/x/.forge/config.toml")

    def save(self, values: object) -> None:  # pragma: no cover - 不应被调用
        raise AssertionError("出错路径不应写入")


def _find(menu: Menu, label: str) -> Choice:
    return next(choice for choice in menu.choices if choice.label == label)


def _models(tmp_path: Path) -> FileLlmConfigService:
    return FileLlmConfigService(TomlLlmConfigStore(tmp_path / ".forge" / "llm.toml"))


def _command(
    tmp_path: Path,
) -> tuple[ConfigCommand, _CapturingPresenter, _RecordingOutput, Path]:
    config_path = tmp_path / ".forge" / "config.toml"
    service = FileConfigService(TomlConfigStore(config_path))
    presenter = _CapturingPresenter()
    output = _RecordingOutput()
    command = ConfigCommand(service, _models(tmp_path), presenter, output)
    return command, presenter, output, config_path


def _execute(command: ConfigCommand) -> None:
    command.execute(SlashCommand(raw_text="/config", command="config"))


def test_execute_presents_menu(tmp_path: Path) -> None:
    command, presenter, _, _ = _command(tmp_path)

    _execute(command)

    assert presenter.presented is not None
    assert presenter.presented.title == "配置"


def test_cycle_choice_writes_through_service(tmp_path: Path) -> None:
    command, presenter, _, config_path = _command(tmp_path)
    _execute(command)
    theme = _find(presenter.presented, "输出主题")

    # 默认 dark，向前切一格 -> light，并落盘。
    assert theme.preview() == "dark"
    theme.on_cycle(1)

    assert config_path.exists()
    assert theme.preview() == "light"


def test_toggle_bool_writes_through_service(tmp_path: Path) -> None:
    command, presenter, _, _ = _command(tmp_path)
    _execute(command)
    telemetry = _find(presenter.presented, "启用使用统计")

    assert telemetry.preview() == "false"
    telemetry.on_cycle(1)
    assert telemetry.preview() == "true"


def test_set_text_path_is_normalized_by_service(tmp_path: Path) -> None:
    command, presenter, _, _ = _command(tmp_path)
    _execute(command)
    workspace = _find(presenter.presented, "工作区目录")

    workspace.on_text("sub/work")

    shown = workspace.preview()
    assert Path(shown).is_absolute()
    # 编辑初值随之更新为已保存的覆盖值。
    assert workspace.text_default() == shown


def test_empty_text_submit_is_ignored(tmp_path: Path) -> None:
    command, presenter, _, config_path = _command(tmp_path)
    _execute(command)
    workspace = _find(presenter.presented, "工作区目录")

    workspace.on_text("   ")

    assert not config_path.exists()


def test_read_error_shows_friendly_message_without_menu(tmp_path: Path) -> None:
    presenter = _CapturingPresenter()
    output = _RecordingOutput()
    command = ConfigCommand(
        FileConfigService(_BrokenStore()), _models(tmp_path), presenter, output
    )

    _execute(command)

    assert presenter.presented is None
    assert output.lines
    assert "语法错误" in output.lines[0]


def test_root_has_provider_and_model_entries(tmp_path: Path) -> None:
    command, presenter, _, _ = _command(tmp_path)
    _execute(command)

    labels = {c.label for c in presenter.presented.choices}
    assert {"供应商配置", "模型配置"} <= labels
    assert _find(presenter.presented, "供应商配置").submenu is not None
    assert _find(presenter.presented, "模型配置").submenu is not None


def test_log_level_lives_in_submenu(tmp_path: Path) -> None:
    command, presenter, _, _ = _command(tmp_path)
    _execute(command)

    advanced = _find(presenter.presented, "日志级别")
    assert advanced.submenu is not None
    submenu = advanced.submenu()
    level = _find(submenu, "日志级别")
    assert level.preview() == "info"
    level.on_cycle(1)
    assert level.preview() in config_keys.require_known(config_keys.LOG_LEVEL).choices
