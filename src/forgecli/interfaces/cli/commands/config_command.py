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
from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.overrides_service import ModelOverridesService
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.domain.config.errors import ConfigReadError
from forgecli.domain.intents import SlashCommand
from forgecli.interfaces.cli.menus.config_menu import ConfigMenu


class ConfigCommand(CommandHandler):
    def __init__(
        self,
        service: ConfigService,
        llm: LlmConfigService,
        presenter: MenuPresenter,
        output: UserOutput,
        *,
        overrides_service: ModelOverridesService | None = None,
    ) -> None:
        self._service = service
        self._llm = llm
        self._presenter = presenter
        self._output = output
        self._menu = ConfigMenu(
            llm_config_service=llm,
            config_service=service,
            output=output,
            overrides_service=overrides_service,
        )

    def execute(self, command: SlashCommand) -> bool:
        # 入口先快照一次：既校验配置文件可读（损坏给友好提示），又作为写入检测基线。
        try:
            before = (
                self._service.effective(),
                self._llm.config(),
                self._llm.runtime_settings_snapshot(),
            )
        except ConfigReadError as exc:
            self._output.print(exc.message)
            return False
        self._presenter.present(self._menu.root_menu())
        # 只有菜单里真的改了某项（config / llm 快照变化）才算写入；只看不改返回 False。
        return (
            self._service.effective(),
            self._llm.config(),
            self._llm.runtime_settings_snapshot(),
        ) != before
