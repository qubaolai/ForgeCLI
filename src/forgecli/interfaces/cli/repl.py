"""可安全退出的交互式会话：读一行 -> IntentRouter 解析 -> 按 intent 分派。

退出方式：/exit、文本 exit/quit/:q、Ctrl-D(EOF)、连按两次 Ctrl-C。
本模块只做"读取 + 分派"，具体命令逻辑在各 handler，按键交互在适配器。
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from forgecli.application.commands.base import SessionState
from forgecli.application.commands.registry import CommandRegistry
from forgecli.application.intent_router import IntentRouter
from forgecli.domain.intents import (
    ControlAction,
    ControlSignal,
    ModeChange,
    SlashCommand,
    UnknownCommand,
    UserIntent,
    UserMessage,
)
from forgecli.interfaces.cli.banner import render_banner
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.prompter import RichOutput
from forgecli.interfaces.cli.wiring import build_registry

# 文本退出词：/exit 之外的便捷退出，保留既有 UX 与 smoke test。
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
        render_banner(console=self._console)
        self._console.print(
            Panel.fit(
                "进入 Forge 交互式会话。\n"
                "输入 [bold]/help[/] 查看命令，[bold]/exit[/] 退出，"
                "连按两次 [bold]Ctrl-C[/] 退出。",
                border_style="cyan",
            )
        )
        interrupt_armed = False  # 第一次 Ctrl-C 后置位；下一次 Ctrl-C 才退出
        while not self._state.should_exit:
            try:
                line = self._console.input("[bold green]forge[/] › ")
            except KeyboardInterrupt:
                if interrupt_armed:
                    self._console.print("\n再见。")
                    break
                interrupt_armed = True
                self._console.print("\n再按一次 Ctrl-C 退出。")
                continue
            except EOFError:
                # Ctrl-D 视为主动退出，不向终端暴露 traceback。
                self._console.print("\n再见。")
                break

            interrupt_armed = False  # 任何正常输入都解除"待退出"
            text = line.strip()
            if not text:
                continue
            if text.lower() in _EXIT_WORDS:
                self._console.print("再见。")
                break

            self._dispatch(self._router.route(text))

    def _dispatch(self, intent: UserIntent) -> None:
        match intent:
            case UserMessage():
                # stub：后续接入应用层会话服务。
                self._output.print(f"[dim](还没有实现)你说了: {intent.text}[/]")
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
            self._console.print("再见。")
            self._state.should_exit = True
        else:  # PAUSE
            self._output.print("已暂停(stub)。")

    def _handle_slash(self, intent: SlashCommand) -> None:
        spec = self._registry.get(intent.command)
        if spec is None or spec.handler is None:
            self._output.print(f"命令 /{intent.command} 暂未实现。")
            return
        spec.handler.execute(intent)


def start_repl() -> None:
    """CLI 入口：装配 REPL 并运行。app.py 仍只调用本函数，无需改动。"""
    console = Console()
    state = SessionState()
    output = RichOutput(console)
    presenter = RichMenuPresenter(console)
    registry = build_registry(state, presenter, output)
    router = IntentRouter(registry)
    Repl(console, router, registry, state, output).run()