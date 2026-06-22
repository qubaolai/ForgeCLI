"""/config 交互式命令（多层菜单）。

职责严格收敛在“交互呈现 + 调用 ConfigService”：
    - 读取展示值：service.effective()
    - 读取编辑初值：service.get()
    - 提交修改：service.set()（校验、合并、持久化都在 service 内）
菜单本身不做配置读取 / 写入 / 合并 / 校验 / 路径归一化等业务。
配置读取错误（如 TOML 语法错误）在入口被翻成一行友好提示，不打开菜单、不抛 traceback。
"""

from __future__ import annotations

from collections.abc import Callable

from forgecli.application.config import keys
from forgecli.application.config.errors import ConfigError, ConfigReadError
from forgecli.application.config.service import ConfigService
from forgecli.application.llm.config.service import LlmConfigService
from forgecli.application.menu import Choice, Menu
from forgecli.application.ports import MenuPresenter, Output
from forgecli.application.slash_commands import CommandHandler
from forgecli.domain.intents import SlashCommand
from forgecli.interfaces.cli.menus.config_options import (
    LOG_LEVEL,
    TELEMETRY,
    THEME,
    WORKSPACE_DIR,
    MenuOption,
)
from forgecli.interfaces.cli.menus.llm_config import LlmMenu


class ConfigCommand(CommandHandler):
    def __init__(
        self,
        service: ConfigService,
        llm: LlmConfigService,
        presenter: MenuPresenter,
        output: Output,
    ) -> None:
        self._service = service
        self._presenter = presenter
        self._output = output
        self._llm_menu = LlmMenu(llm, output)

    def execute(self, command: SlashCommand) -> None:
        # 入口先触发一次读取：配置文件损坏时给友好提示，而不是进菜单后崩。
        try:
            self._service.effective()
        except ConfigReadError as exc:
            self._output.print(exc.message)
            return
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
                Choice("日志级别", submenu=self._advanced_menu),
                Choice("供应商配置", submenu=self._llm_menu.providers_menu),
                Choice("模型配置", submenu=self._llm_menu.models_menu),
            ),
        )

    def _advanced_menu(self) -> Menu:
        return Menu(
            "日志级别",
            (
                Choice(
                    "日志级别",
                    preview=self._shown(LOG_LEVEL),
                    on_cycle=self._cycle_choice(LOG_LEVEL),
                ),
            ),
        )

    # ---- 回调工厂（全部委托 service）----

    def _raw(self, option: MenuOption) -> Callable[[], str]:
        # 编辑文本框的初值：用户覆盖原值，未覆盖则空串。
        return lambda: self._service.get(option.key) or ""

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

    def _set_text(self, option: MenuOption) -> Callable[[str], None]:
        def submit(value: str) -> None:
            if value.strip():
                self._safe_set(option, value)

        return submit

    def _safe_set(self, option: MenuOption, value: str) -> None:
        # 校验失败等业务错误翻成一行提示，菜单继续运行。
        try:
            self._service.set(option.key, value)
        except ConfigError as exc:
            self._output.print(exc.message)
