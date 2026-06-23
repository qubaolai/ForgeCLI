"""可安全退出的交互式会话：读一行 -> IntentRouter 解析 -> 按 intent 分派。

退出方式：/exit、文本 exit/quit/:q、Ctrl-D(EOF)、空行连按两次 Ctrl-C。
本模块只做"读取 + 分派"，具体命令逻辑在各 handler，按键交互在适配器。

交互式会话需要真终端(TTY)。非终端(管道 / CI / 测试)下 prompt_toolkit 的全屏输入
无法工作，此时直接拒绝并退出，而不是降级成一个变差的读取器——判断标准与
menu_presenter 一致，都用 stdin_is_tty()。
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.panel import Panel

from forgecli.application.intent_router import IntentRouter
from forgecli.application.session import SessionState
from forgecli.application.slash_commands import CommandRegistry
from forgecli.domain.intents import (
    ControlAction,
    ControlSignal,
    ModeChange,
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

# 文本退出词保留在 REPL 层：它们是交互体验快捷方式，不属于 slash command。
_EXIT_WORDS = {"exit", "quit", ":q"}


class Repl:
    def __init__(
        self,
        console: Console,
        router: IntentRouter,
        registry: CommandRegistry,
        state: SessionState,
        output: RichOutput,
    ) -> None:
        self._console = console
        self._router = router
        self._registry = registry
        self._state = state
        self._output = output

    def run(self) -> None:
        # banner 由 bootstrap 在信任解析前渲染；这里只给进入会话的提示。
        self._console.print(
            Panel.fit(
                "进入 Forge 交互式会话。\n"
                "输入 [bold]/help[/] 查看命令，[bold]/exit[/] 退出，"
                "空行连按两次 [bold]Ctrl-C[/] 退出。",
                border_style="cyan",
            )
        )
        # prompt_toolkit 的底层 Application 依赖真 TTY；管道和测试场景直接安全退出。
        if not stdin_is_tty():
            self._console.print("[yellow]Forge 交互式会话需要在终端(TTY)中运行。[/]")
            return

        # 输入框只需要命令名和说明，用于 "/" 补全菜单；执行仍由 registry 分派。
        commands = [(spec.name, spec.summary) for spec in self._registry.all_specs()]
        prompt = ForgePrompt(commands)
        while not self._state.should_exit:
            try:
                line = prompt.read()
            except QuitSignal:
                # 空行连按两次 Ctrl-C，视为主动退出。
                break
            except EOFError:
                # 空行 Ctrl-D，视为主动退出，不向终端暴露 traceback。
                break
            self._process_line(line)

    def _process_line(self, line: str) -> None:
        """清洗一行输入并分派：空行忽略、退出词退出、其余交给 IntentRouter。"""
        text = line.strip()
        if not text:
            return
        if text.lower() in _EXIT_WORDS:
            self._console.print("再见。")
            self._state.should_exit = True
            return
        self._dispatch(self._router.route(text))

    def _dispatch(self, intent: UserIntent) -> None:
        match intent:
            case UserMessage():
                # 同屏显示一轮对话：先回显用户输入(绿)，再给助手输出(青绿)。
                render_user_turn(self._console, intent.text)
                # 真正的 LLM 调用接在这里(适配器尚未装配)；先占住助手那一轮。
                with thinking(self._console, label="runing...."):
                    # 真正的 LLM 调用接这里;loading 会持续到这个 with 块结束。
                    time.sleep(2)
                    reply = "(LLM 接入开发中)已收到你的消息。"
                render_assistant_turn(self._console, reply)
            case ModeChange():
                self._state.mode = intent.target_mode
                self._output.print(
                    f"已切换到 [bold]{intent.target_mode.value}[/] 模式。"
                )
            case ControlSignal():
                self._handle_control(intent)
            case SlashCommand():
                self._handle_slash(intent)
            case UnknownCommand():
                self._output.print(intent.error_message)
            case _:
                self._output.print("无法处理的输入。")

    def _handle_control(self, intent: ControlSignal) -> None:
        if intent.action is ControlAction.EXIT:
            self._state.should_exit = True
        else:  # PAUSE
            self._output.print("已暂停(stub)。")

    def _handle_slash(self, intent: SlashCommand) -> None:
        spec = self._registry.get(intent.command)
        if spec is None or spec.handler is None:
            self._output.print(f"命令 /{intent.command} 暂未实现。")
            return
        spec.handler.execute(intent)
