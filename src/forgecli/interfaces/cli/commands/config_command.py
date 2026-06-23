"""/config 交互式命令（多层菜单）。

职责严格收敛在“交互呈现 + 调用 ConfigService”：
    - 读取展示值：service.effective()
    - 读取编辑初值：service.get()
    - 提交修改：service.set()（校验、合并、持久化都在 service 内）
菜单本身不做配置读取 / 写入 / 合并 / 校验 / 路径归一化等业务。
配置读取错误（如 TOML 语法错误）在入口被翻成一行友好提示，不打开菜单、不抛 traceback。
"""

from __future__ import annotations

from forgecli.application.config.config_service import ConfigService
from forgecli.application.config.errors import ConfigReadError
from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.slash_commands import CommandHandler
from forgecli.domain.intents import SlashCommand
from forgecli.interfaces.cli.menus.config_menu import ConfigMenu


class ConfigCommand(CommandHandler):
    def __init__(
        self,
        service: ConfigService,
        llm: LlmConfigService,
        presenter: MenuPresenter,
        output: UserOutput,
    ) -> None:
        self._service = service
        self._presenter = presenter
        self._output = output
        self._menu = ConfigMenu(
            llm_config_service=llm, config_service=service, output=output
        )

    def execute(self, command: SlashCommand) -> None:
        # 入口先触发一次读取：配置文件损坏时给友好提示，而不是进菜单后崩。
        try:
            self._service.effective()
        except ConfigReadError as exc:
            self._output.print(exc.message)
            return
        self._presenter.present(self._menu.root_menu())
