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
        rows: list[Choice] = [
            Choice("常规配置", submenu=self._general_menu),
            Choice("供应商配置", submenu=self._llm_menu.providers_menu),
            Choice("模型配置", submenu=self._llm_menu.models_menu),
            Choice("网关运行时配置", submenu=self._gateway_menu.root_menu),
        ]
        if self._overrides_menu is not None:
            rows.append(Choice("用途模型覆盖", submenu=self._overrides_menu.root_menu))
        return Menu("配置", tuple(rows))

    def _general_menu(self) -> Menu:
        """应用级配置, 一行一个键, 全部从 SCHEMA 派生.

        原先这里只手写了一行"日志级别", 别的应用级键在终端里根本没有入口 —— 要改就得
        自己去找 config.json. 而每加一个键就手写一行的做法, 会让终端和网页各有一份中文
        文案, 同一个开关在两处叫不同名字.

        名字与说明现在都住在 `ConfigKey` 上 (domain/config/config_keys), 这里只负责
        "按类型挑一种交互": 布尔按左右切, 枚举按左右轮, 文本与整数进行内编辑.
        """
        rows: list[Choice] = []
        for key in keys.keys_for(keys.ConfigLevel.APP):
            option = MenuOption(key.title, key.name)
            if key.kind is keys.ValueKind.BOOL:
                rows.append(
                    Choice(
                        key.title,
                        preview=self._shown(option),
                        on_cycle=self._cycle_bool(option),
                        payload=self._help(key),
                    )
                )
            elif key.kind is keys.ValueKind.CHOICE:
                rows.append(
                    Choice(
                        key.title,
                        preview=self._shown(option),
                        on_cycle=self._cycle_choice(option),
                        payload=self._help(key),
                    )
                )
            else:
                rows.append(
                    Choice(
                        key.title,
                        preview=self._shown(option),
                        on_text=self._set_text(option),
                        text_default=self._shown(option),
                        payload=self._help(key),
                    )
                )
        return Menu("常规配置", tuple(rows))

    @staticmethod
    def _help(key: keys.ConfigKey) -> Callable[[], str] | None:
        """空格键展开的那段说明. 没写说明就不给这一行加预览."""
        return (lambda: key.help) if key.help else None

    def _set_text(self, option: MenuOption) -> Callable[[str], None]:
        return lambda value: self._safe_set(option, value)

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
