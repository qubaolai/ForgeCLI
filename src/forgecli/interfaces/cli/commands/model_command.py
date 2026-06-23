"""/model 运行时默认模型选择面板。"""

from __future__ import annotations

from forgecli.application.config.config_service import ConfigService
from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.llm.availability import EnvProviderAvailability
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.domain.intents import SlashCommand
from forgecli.interfaces.cli.menus.model_menu import ModelsMenu
from forgecli.shared.errors import ConfigReadError


class ModelsCommand(CommandHandler):
    def __init__(
        self,
        config: ConfigService,
        llm: LlmConfigService,
        presenter: MenuPresenter,
        output: UserOutput,
    ) -> None:
        self._presenter = presenter
        self._config = config
        self._llm = llm
        self._output = output
        self._menu = ModelsMenu(llm, EnvProviderAvailability(), config, output)

    def execute(self, command: SlashCommand) -> None:
        if command.args:
            self._output.print(
                "当前 /model 不支持参数；请直接输入 /model 打开模型选择面板。"
            )
            return
        try:
            self._config.effective()  # 触发读取：损坏配置在进菜单前就友好报错
            self._llm.config()
        except ConfigReadError as exc:
            self._output.print(exc.message)
            return
        self._presenter.present(self._menu.root_menu())
