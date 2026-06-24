"""配置命令菜单面板（手写层级，统一走 ConfigService）。

每个配置项的展示与编辑都委托同一个 ConfigService：`display(key)` 读、`set(key,v)` 写，
按该键在 SCHEMA 里的 `level` 自动路由到 config.toml 或 forge.toml。菜单只声明"哪个键
放在哪一层、用什么交互"，加配置项 = 加一行 Choice，不写新回调（开闭原则）。

工作区目录与默认模型不在此编辑——分别走 `/add-dir` 与 `/model`。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from forgecli.application.config import config_keys as keys
from forgecli.application.config.config_service import ConfigService
from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.menu import Choice, Menu
from forgecli.interfaces.cli.menus.llm_menu import LlmMenu
from forgecli.shared.errors import ConfigError


@dataclass(frozen=True)
class MenuOption:
    label: str  # 菜单展示文案
    key: str  # 对应 keys.SCHEMA 中的配置键名


TELEMETRY = MenuOption("启用使用统计", keys.TELEMETRY_ENABLED)
THEME = MenuOption("输出主题", keys.OUTPUT_THEME)
LOG_LEVEL = MenuOption("日志级别", keys.LOGGING_LEVEL)


class ConfigMenu:
    def __init__(
        self,
        llm_config_service: LlmConfigService,
        config_service: ConfigService,
        output: UserOutput,
    ) -> None:
        self._llm_config_service = llm_config_service
        self._service = config_service
        self._output = output
        self._llm_menu = LlmMenu(llm_config_service, output)

    def root_menu(self) -> Menu:
        return Menu(
            "配置",
            (
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
                Choice(
                    "日志级别",
                    preview=self._shown(LOG_LEVEL),
                    on_cycle=self._cycle_choice(LOG_LEVEL),
                ),
                Choice("供应商配置", submenu=self._llm_menu.providers_menu),
                Choice("模型配置", submenu=self._llm_menu.models_menu),
            ),
        )

    # ---- 泛型回调（按 kind，统一委托 ConfigService，按 level 路由落盘）----

    def _shown(self, option: MenuOption) -> Callable[[], str]:
        return lambda: self._service.display(option.key)

    def _cycle_bool(self, option: MenuOption) -> Callable[[int], None]:
        def cycle(_delta: int) -> None:
            now = self._service.display(option.key) == "true"
            self._safe_set(option, "false" if now else "true")

        return cycle

    def _cycle_choice(self, option: MenuOption) -> Callable[[int], None]:
        def cycle(delta: int) -> None:
            choices = keys.require_known(option.key).choices
            if not choices:
                return
            current = self._service.display(option.key)
            idx = choices.index(current) if current in choices else 0
            self._safe_set(option, choices[(idx + delta) % len(choices)])

        return cycle

    def _safe_set(self, option: MenuOption, value: str) -> None:
        # 校验失败等业务错误翻成一行提示，菜单继续运行。
        try:
            self._service.set(option.key, value)
        except ConfigError as exc:
            self._output.print(exc.message)
