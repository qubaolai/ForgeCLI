"""/config 交互式命令(多层菜单)。菜单结构见各 _xxx_menu 方法。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from forgecli.application.commands.base import CommandHandler
from forgecli.application.commands.config.options import (
    LOG_LEVEL,
    THEME,
    TELEMETRY,
    WORKSPACE_DIR,
    ConfigOption,
    OptionType,
)
from forgecli.application.config_service import ConfigService
from forgecli.application.menu import Choice, Menu
from forgecli.application.ports import MenuPresenter, Output
from forgecli.domain.intents import SlashCommand


def _as_bool(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


class ConfigCommand(CommandHandler):
    def __init__(
        self, service: ConfigService, presenter: MenuPresenter, output: Output
    ) -> None:
        self._service = service
        self._presenter = presenter
        self._output = output

    def execute(self, command: SlashCommand) -> None:
        self._presenter.present(self._root_menu())

    def _root_menu(self) -> Menu:
        return Menu(
            "配置",
            (
                Choice(
                    "工作区目录",
                    preview=self._shown(WORKSPACE_DIR),
                    on_text=self._set_text(WORKSPACE_DIR),
                    text_default=self._raw(WORKSPACE_DIR),
                ),
                Choice(
                    "启用使用统计",
                    preview=self._shown(TELEMETRY),
                    on_cycle=self._cycle_bool(TELEMETRY),
                ),
                Choice(
                    "输出主题",
                    preview=self._shown(THEME),
                    on_cycle=self._cycle_choice(THEME),
                ),
                Choice("高级…", submenu=self._advanced_menu),
            ),
        )

    def _advanced_menu(self) -> Menu:
        return Menu(
            "高级",
            (
                Choice(
                    "日志级别",
                    preview=self._shown(LOG_LEVEL),
                    on_cycle=self._cycle_choice(LOG_LEVEL),
                ),
            ),
        )

    # ---- 回调工厂 ----

    def _raw(self, option: ConfigOption) -> Callable[[], str]:
        return lambda: self._service.get(option.key) or ""

    def _shown(self, option: ConfigOption) -> Callable[[], str]:
        return lambda: self._service.get(option.key) or "(未设置)"

    def _cycle_bool(self, option: ConfigOption) -> Callable[[int], None]:
        def cycle(_delta: int) -> None:
            now = _as_bool(self._service.get(option.key))
            self._service.set(option.key, "false" if now else "true")

        return cycle

    def _cycle_choice(self, option: ConfigOption) -> Callable[[int], None]:
        def cycle(delta: int) -> None:
            choices = option.choices
            if not choices:
                return
            current = self._service.get(option.key)
            idx = choices.index(current) if current in choices else 0
            self._service.set(option.key, choices[(idx + delta) % len(choices)])

        return cycle

    def _set_text(self, option: ConfigOption) -> Callable[[str], None]:
        def submit(value: str) -> None:
            value = value.strip()
            if not value:
                return
            if option.type is OptionType.PATH:
                path = Path(value).expanduser()
                value = str(path if path.is_absolute() else (Path.cwd() / path).resolve())
            self._service.set(option.key, value)

        return submit