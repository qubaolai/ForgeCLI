"""配置命令菜单面板"""

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


# 工作区目录与日志级别已移交项目级配置（ADR-0008），不再出现在 /config。
TELEMETRY = MenuOption("启用使用统计", keys.TELEMETRY_ENABLED)
THEME = MenuOption("输出主题", keys.OUTPUT_THEME)


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
                Choice("供应商配置", submenu=self._llm_menu.providers_menu),
                Choice("模型配置", submenu=self._llm_menu.models_menu),
            ),
        )

    # ---- 回调工厂（全部委托 service）----

    def _shown(self, option: MenuOption) -> Callable[[], str]:
        # 菜单右侧展示：当前有效值（含默认回落）。
        return lambda: self._service.effective().display(option.key)

    def _cycle_bool(self, option: MenuOption) -> Callable[[int], None]:
        def cycle(_delta: int) -> None:
            now = self._service.effective().display(option.key) == "true"
            self._safe_set(option, "false" if now else "true")

        return cycle

    def _cycle_choice(self, option: MenuOption) -> Callable[[int], None]:
        def cycle(delta: int) -> None:
            choices = keys.require_known(option.key).choices
            if not choices:
                return
            current = self._service.effective().display(option.key)
            idx = choices.index(current) if current in choices else 0
            self._safe_set(option, choices[(idx + delta) % len(choices)])

        return cycle

    def _safe_set(self, option: MenuOption, value: str) -> None:
        # 校验失败等业务错误翻成一行提示，菜单继续运行。
        try:
            self._service.set(option.key, value)
        except ConfigError as exc:
            self._output.print(exc.message)
