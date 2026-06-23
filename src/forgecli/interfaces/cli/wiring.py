"""组合根：装配 registry / handlers / router / prompter。

只有这里知道全部具体实现。新增命令 = 在这里 register 一条 spec。
"""

from __future__ import annotations

from forgecli.application.config.config_service import FileConfigService
from forgecli.application.llm.config.llm_config_service import FileLlmConfigService
from forgecli.application.session import SessionState
from forgecli.application.slash_commands import CommandRegistry, CommandSpec
from forgecli.domain.intents import ControlAction, IntentKind, SessionMode
from forgecli.infrastructure.config import TomlConfigStore, config_file
from forgecli.infrastructure.llm.config import TomlLlmConfigStore
from forgecli.interfaces.cli.commands.config_command import ConfigCommand
from forgecli.interfaces.cli.commands.help_command import HelpCommand
from forgecli.interfaces.cli.commands.model_command import ModelsCommand
from forgecli.interfaces.cli.commands.status_command import StatusCommand
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput


def build_registry(
    state: SessionState, presenter: RichMenuPresenter, output: RichOutput
) -> CommandRegistry:
    registry = CommandRegistry()
    # 配置文件放用户级目录（默认 ~/.forge，可用 FORGE_CONFIG_DIR 覆盖），
    # 与工作空间无关；不预先创建，首次 /config 修改时才写出。
    config_service = FileConfigService(TomlConfigStore(config_file("config.toml")))
    llm_service = FileLlmConfigService(TomlLlmConfigStore(config_file("llm.toml")))

    mode_specs = [
        CommandSpec(
            "chat", IntentKind.MODE_CHANGE, "切换到对话模式", mode=SessionMode.CHAT
        ),
        CommandSpec(
            "plan", IntentKind.MODE_CHANGE, "切换到计划模式", mode=SessionMode.PLAN
        ),
        CommandSpec(
            "act", IntentKind.MODE_CHANGE, "切换到执行模式", mode=SessionMode.ACT
        ),
    ]
    control_specs = [
        CommandSpec(
            "pause", IntentKind.CONTROL, "暂停当前任务", action=ControlAction.PAUSE
        ),
        CommandSpec("exit", IntentKind.CONTROL, "退出会话", action=ControlAction.EXIT),
    ]
    slash_specs = [
        CommandSpec(
            "status",
            IntentKind.SLASH_COMMAND,
            "查看状态",
            handler=StatusCommand(registry, output),
        ),
        CommandSpec(
            "help",
            IntentKind.SLASH_COMMAND,
            "查看可用命令或某命令帮助",
            handler=HelpCommand(registry, output),
        ),
        CommandSpec(
            "config",
            IntentKind.SLASH_COMMAND,
            "查看 / 修改配置",
            handler=ConfigCommand(config_service, llm_service, presenter, output),
        ),
        CommandSpec(
            "model",
            IntentKind.SLASH_COMMAND,
            "打开运行时默认模型选择面板",
            handler=ModelsCommand(config_service, llm_service, presenter, output),
        ),
    ]
    registry.register_all([*mode_specs, *control_specs, *slash_specs])
    return registry
