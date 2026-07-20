"""可安全退出的交互式会话：读一行 -> IntentRouter 解析 -> 按 intent 分派。

退出方式：空行连按两次 Ctrl-C。
本模块只做"读取 + 分派 + 记录会话事件"，具体命令逻辑在各 handler，按键交互在适配器。

会话事件（27 日）：进入 REPL 即 start() 当前 session（惰性落盘，无操作不写文件）；
自然语言写 user_message、模式切换写 mode_changed；斜杠命令事件类型先保留，
本日暂不落盘记录。

交互式会话需要真终端(TTY)。非终端(管道 / CI / 测试)下 prompt_toolkit 的全屏输入
无法工作，此时直接拒绝并退出，而不是降级成一个变差的读取器——判断标准与
menu_presenter 一致，都用 stdin_is_tty()。
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.intent_router import IntentRouter
from forgecli.application.session import SessionService
from forgecli.application.slash_commands import CommandRegistry
from forgecli.domain.intents import (
    SlashCommand,
    UnknownCommand,
    UserIntent,
    UserMessage,
)
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.prompt_loop import ForgePrompt, QuitSignal
from forgecli.interfaces.cli.transcript import (
    render_assistant_turn,
    render_user_turn,
    thinking,
)
from forgecli.interfaces.cli.tty.tty import stdin_is_tty


class Repl:
    def __init__(
        self,
        console: Console,
        router: IntentRouter,
        registry: CommandRegistry,
        output: RichOutput,
        session: SessionService,
        agent_turn: AgentTurnService,
        *,
        prompt_status: Callable[[], str] | None = None,
    ) -> None:
        self._console = console
        self._router = router
        self._registry = registry
        self._output = output
        self._session = session
        self._agent_turn = agent_turn
        self._prompt_status = prompt_status

    def run(self) -> None:
        # banner 由 bootstrap 在信任解析前渲染；这里只给进入会话的提示。
        self._console.print(
            Panel.fit(
                "进入 Forge 交互式会话。\n"
                "输入 [bold]/help[/] 查看命令，"
                "空行连按两次 [bold]Ctrl-C[/] 退出。",
                border_style="cyan",
            )
        )
        # prompt_toolkit 的底层 Application 依赖真 TTY；管道和测试场景直接安全退出。
        if not stdin_is_tty():
            self._console.print("[yellow]Forge 交互式会话需要在终端(TTY)中运行。[/]")
            return

        # 进入交互式会话即开启当前 session（惰性落盘：无可记录动作则不写文件）。
        self._session.start()
        # 输入框只需要命令名和说明，用于 "/" 补全菜单；执行仍由 registry 分派。
        commands = [(spec.name, spec.summary) for spec in self._registry.all_specs()]
        prompt = (
            ForgePrompt(commands)
            if self._prompt_status is None
            else ForgePrompt(commands, status_provider=self._prompt_status)
        )
        while True:
            try:
                line = prompt.read()
            except QuitSignal:
                # 主动退出，不向终端暴露 traceback。
                break
            self._process_line(line)

    def _process_line(self, line: str) -> None:
        """清洗一行输入并分派：空行忽略、退出词退出、其余交给 IntentRouter。"""
        text = line.strip()
        if not text:
            return
        self._dispatch(self._router.route(text))

    def _dispatch(self, intent: UserIntent) -> None:
        match intent:
            case UserMessage():
                # 同屏显示一轮对话：先回显用户输入(绿)，再给助手输出(青绿)。
                render_user_turn(self._console, intent.text)
                with thinking(self._console, label="runing...."):
                    response = self._agent_turn.handle_user_message(intent.text)
                render_assistant_turn(self._console, response.text)
            case SlashCommand():
                self._handle_slash(intent)
            case UnknownCommand():
                self._output.print(intent.error_message)
            case _:
                self._output.print("无法处理的输入。")

    def _handle_slash(self, intent: SlashCommand) -> None:
        spec = self._registry.get(intent.command)
        if spec is None or spec.handler is None:
            self._output.print(f"命令 /{intent.command} 暂未实现。")
            return
        # 27 日暂不记录 slash_command 事件；后续由 handler 返回值决定是否落盘。
        # if spec.handler.execute(intent):
        spec.handler.execute(intent)
        # self._session.record_slash_command(intent.command, intent.args)
