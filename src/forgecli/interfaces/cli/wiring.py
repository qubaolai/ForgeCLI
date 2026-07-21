"""组合根：装配 registry / handlers / router / prompter。

只有这里知道全部具体实现。新增命令 = 在这里 register 一条 spec。
是否记录会话事件不在此声明：由各 handler 执行后是否真正发生持久化写入决定。
"""

from __future__ import annotations

from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.config.config_service import ConfigService
from forgecli.application.interaction_ports import DirectoryPicker
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.overrides_service import ModelOverridesService
from forgecli.application.project import ProjectContext, ProjectService
from forgecli.application.session import ResumeService, SessionService
from forgecli.application.slash_commands import CommandRegistry, CommandSpec
from forgecli.domain.intents import SessionMode
from forgecli.infrastructure.config import TomlConfigStore, config_dir, config_file
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.infrastructure.llm.overrides_toml_store import TomlModelOverridesStore
from forgecli.infrastructure.session import (
    FsSessionCatalog,
    JsonlEventStore,
    JsonStateStore,
)
from forgecli.interfaces.cli.commands.add_dir_command import AddDirCommand
from forgecli.interfaces.cli.commands.config_command import ConfigCommand
from forgecli.interfaces.cli.commands.help_command import HelpCommand
from forgecli.interfaces.cli.commands.mode_command import ModeCommand
from forgecli.interfaces.cli.commands.model_command import ModelsCommand
from forgecli.interfaces.cli.commands.resume_command import ResumeCommand
from forgecli.interfaces.cli.commands.status_command import StatusCommand
from forgecli.interfaces.cli.commands.thinking_command import ThinkingCommand
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput


def build_registry(
    session_service: SessionService,
    context: ProjectContext,
    project_service: ProjectService,
    presenter: RichMenuPresenter,
    picker: DirectoryPicker,
    output: RichOutput,
    agent_turn: AgentTurnService,
    *,
    config_service: ConfigService | None = None,
    llm_service: LlmConfigService | None = None,
    overrides_service: ModelOverridesService | None = None,
) -> CommandRegistry:
    registry = CommandRegistry()
    # ConfigService 按 level 路由落盘：应用级 config.toml，项目级当前项目的 forge.toml。
    # 都在用户级 Forge home 下；不预先创建，首次写配置时才落盘。
    # bootstrap 会传入与 LLM 网关共享的同一批 service；未传入时（测试直连）自行构建。
    project_home = config_dir() / "projects" / context.project.project_id
    forge_toml = project_home / "forge.toml"
    if config_service is None:
        config_service = ConfigService(
            TomlConfigStore(config_file("config.toml")),
            TomlConfigStore(forge_toml),
        )
    if llm_service is None:
        llm_service = LlmConfigService(TomlLlmConfigStore(config_file("llm.toml")))
    if overrides_service is None:
        overrides_service = ModelOverridesService(
            TomlModelOverridesStore(forge_toml), llm_service
        )

    # resume 复用 application service：枚举 / 搜索 / 读取本项目 sessions/ 下的历史会话。
    sessions_dir = project_home / "sessions"
    resume_service = ResumeService(
        FsSessionCatalog(sessions_dir),
        JsonStateStore(sessions_dir),
        JsonlEventStore(sessions_dir),
    )

    # 模式切换归一为普通斜杠命令：三条共用 ModeCommand，靠构造参数区分目标模式。
    mode_specs = [
        CommandSpec(
            "chat",
            "切换到对话模式",
            handler=ModeCommand(SessionMode.CHAT, session_service, output),
        ),
        CommandSpec(
            "plan",
            "切换到计划模式",
            handler=ModeCommand(SessionMode.PLAN, session_service, output),
        ),
        CommandSpec(
            "act",
            "切换到执行模式",
            handler=ModeCommand(SessionMode.ACT, session_service, output),
        ),
    ]
    slash_specs = [
        CommandSpec(
            "status",
            "查看状态",
            handler=StatusCommand(session_service, context, output),
        ),
        CommandSpec(
            "help",
            "查看可用命令或某命令帮助",
            handler=HelpCommand(registry, output),
        ),
        CommandSpec(
            "config",
            "查看 / 修改配置",
            handler=ConfigCommand(
                config_service,
                llm_service,
                presenter,
                output,
                overrides_service=overrides_service,
            ),
        ),
        CommandSpec(
            "model",
            "打开运行时默认模型选择面板",
            handler=ModelsCommand(config_service, llm_service, presenter, output),
        ),
        CommandSpec(
            "thinking",
            "查看 / 修改当前模型的思考开关与强度",
            handler=ThinkingCommand(config_service, llm_service, output),
        ),
        CommandSpec(
            "add-dir",
            "添加可操作工作区目录",
            handler=AddDirCommand(context, project_service, picker, output),
        ),
        CommandSpec(
            "resume",
            "查看 / 恢复历史会话",
            handler=ResumeCommand(
                resume_service,
                session_service,
                agent_turn,
                context,
                presenter,
                output,
            ),
        ),
    ]
    registry.register_all([*mode_specs, *slash_specs])
    return registry
