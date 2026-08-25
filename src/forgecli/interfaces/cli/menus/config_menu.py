"""配置命令菜单面板（手写层级，统一走 ConfigService）。

每个配置项的展示与编辑都委托同一个 ConfigService：`display(key)` 读、`set(key,v)` 写，
按该键在 SCHEMA 里的 `level` 自动路由到 config.json 或 forge.json。菜单只声明"哪个键
放在哪一层、用什么交互"，加配置项 = 加一行 Choice，不写新回调（开闭原则）。

工作区目录与默认模型不在此编辑——分别走 `/add-dir` 与 `/model`。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from forgecli.application.config.config_service import ConfigService
from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.overrides_service import ModelOverridesService
from forgecli.application.menu import Choice, Menu
from forgecli.domain.config import config_keys as keys
from forgecli.interfaces.cli.menus.gateway_config_menu import GatewayConfigMenu
from forgecli.interfaces.cli.menus.llm_menu import LlmMenu
from forgecli.interfaces.cli.menus.overrides_menu import OverridesMenu
from forgecli.shared.errors import ConfigError


@dataclass(frozen=True)
class MenuOption:
    label: str  # 菜单展示文案
    key: str  # 对应 keys.SCHEMA 中的配置键名


class ConfigMenu:
    def __init__(
        self,
        llm_config_service: LlmConfigService,
        config_service: ConfigService,
        output: UserOutput,
        overrides_service: ModelOverridesService | None = None,
    ) -> None:
        self._llm_config_service = llm_config_service
        self._service = config_service
        self._output = output
        self._llm_menu = LlmMenu(llm_config_service, output)
        self._gateway_menu = GatewayConfigMenu(llm_config_service, output)
        self._overrides_menu = (
            OverridesMenu(overrides_service, llm_config_service, output)
            if overrides_service is not None
            else None
        )

    def root_menu(self) -> Menu:
        # 日志级别就地切换 (ADR-0035): 一个只能靠改 config.json 才能打开的 debug
        # 开关, 等于要求用户在最需要日志的那一刻先去找配置文件.
        # 改完下次启动生效 —— 当前进程的 handler 在启动时就装好了.
        log_level = MenuOption(
            "日志级别 (debug/info/warn, 下次启动生效)", keys.LOGGING_LEVEL
        )
        rows: list[Choice] = [
            Choice("供应商配置", submenu=self._llm_menu.providers_menu),
            Choice("模型配置", submenu=self._llm_menu.models_menu),
            Choice("网关运行时配置", submenu=self._gateway_menu.root_menu),
            Choice(
                log_level.label,
                preview=self._shown(log_level),
                on_cycle=self._cycle_choice(log_level),
            ),
        ]
        if self._overrides_menu is not None:
            rows.append(Choice("用途模型覆盖", submenu=self._overrides_menu.root_menu))
        return Menu("配置", tuple(rows))

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
