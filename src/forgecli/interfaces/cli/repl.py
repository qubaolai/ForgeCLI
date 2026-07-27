"""可安全退出的交互式会话：读一行 -> IntentRouter 解析 -> 按 intent 分派。

退出方式：空行连按两次 Ctrl-C。
本模块只做"读取 + 分派 + 记录会话事件"，具体命令逻辑在各 handler，按键交互在适配器。

会话事件：进入 REPL 即 start() 当前 session（惰性落盘，无操作不写文件）；
自然语言写 user_message、模式切换写 mode_changed；斜杠命令按 handler 返回值
落盘 slash_command——只有真正写了持久状态的命令才记录，只读命令不记。

交互式会话需要真终端(TTY)。非终端(管道 / CI / 测试)下 prompt_toolkit 的全屏输入
无法工作，此时直接拒绝并退出，而不是降级成一个变差的读取器——判断标准与
menu_presenter 一致，都用 stdin_is_tty()。
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.agent_turn.cancellation import TurnCancelSource
from forgecli.application.agent_turn.turn import AssistantResponse
from forgecli.application.intent_router import IntentRouter
from forgecli.application.llm.gateway.request import CancelToken
from forgecli.application.session import SessionService
from forgecli.application.slash_commands import CommandRegistry
from forgecli.domain.conversation import TurnStatus
from forgecli.domain.intents import (
    SlashCommand,
    UnknownCommand,
    UserIntent,
    UserMessage,
)
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.prompt_loop import ForgePrompt, QuitSignal
from forgecli.interfaces.cli.stream_render import StreamingTranscript
from forgecli.interfaces.cli.transcript import (
    assistant_notice,
    render_assistant_turn,
    render_user_turn,
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
        stream_view: StreamingTranscript,
        cancel_source: TurnCancelSource,
        prompt_status: Callable[[], str] | None = None,
    ) -> None:
        self._console = console
        self._router = router
        self._registry = registry
        self._output = output
        self._session = session
        self._agent_turn = agent_turn
        self._stream_view = stream_view
        self._cancel_source = cancel_source
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
        # prompt_toolkit 的底层 Application 依赖真 TTY。产品路径上 bootstrap 已在最
        # 前面拒掉非 TTY (退出码 NO_TTY), 这里只是直接构造 Repl 的调用方的兜底。
        if not stdin_is_tty():
            self._console.print("[yellow]Forge 交互式会话需要在终端(TTY)中运行。[/]")
            return

        # 进入交互式会话即开启当前 session（惰性落盘：无可记录动作则不写文件）。
        self._session.start()
        # 输入框只需要命令名和说明，用于 "/" 补全菜单；执行仍由 registry 分派。
        commands = [(spec.name, spec.summary) for spec in self._registry.all_specs()]
        prompt = ForgePrompt(
            commands,
            status_provider=self._prompt_status,
            on_mode_step=self._step_mode,
        )
        while True:
            try:
                line = prompt.read()
            except QuitSignal:
                # 主动退出，不向终端暴露 traceback。
                break
            except EOFError:
                # 输入流结束（stdin 被关闭等）：已经读不到下一行，安全收尾。
                # 空行 Ctrl-D 不走这里——prompt 的 c-d 绑定消费掉了它（退出只认
                # Ctrl-C×2，ADR-0007），所以这里不是退出快捷键，只是流末尾兜底。
                break
            self._process_line(line)

    def _step_mode(self, step: int) -> None:
        """shift+tab 回调: 切到下一档模式并落 mode_changed 事件.

        与 /plan 一类模式命令走同一条落盘路径, 事件日志里两者不可区分 —— 模式的
        真相源始终是 session 快照, 而不是某个 UI 状态.
        """
        current = self._session.current().mode
        target = current.step(step)
        if target is not current:
            self._session.set_mode(target)

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
                # 处理期间流式正文 append-only 逐行提交；收尾按终态追加提示。
                render_user_turn(self._console, intent.text)
                response = self._run_turn(intent.text)
                self._finish_render(response)
            case SlashCommand():
                self._handle_slash(intent)
            case UnknownCommand():
                self._output.print(intent.error_message)
            case _:
                self._output.print("无法处理的输入。")

    def _run_turn(self, text: str) -> AssistantResponse:
        """跑一轮 agent turn：挂取消 token + SIGINT 接线 + 流式渲染。

        finish() 在 Live 激活期间调用，把最后半行正文定稿提交；收尾提示留到
        turn 之后（Live 已擦除）由 _finish_render 追加。
        """
        token = self._cancel_source.issue()
        restore = _install_sigint(token)
        try:
            with self._stream_view.turn():
                response = self._agent_turn.handle_user_message(text)
                self._stream_view.finish()
            return response
        finally:
            restore()
            self._cancel_source.clear()

    def _finish_render(self, response: AssistantResponse) -> None:
        """turn 收尾渲染：流式正文已 append，这里只补分隔或追加收尾提示。

        - 正常轮：正文已逐行打完，仅补一空行分隔下一轮；若本轮无流式输出
          （非流式 loop / 零增量），整块打印正文。
        - 失败 / 取消轮：在已打正文之后换行追加灰色提示——取消轮去掉与正文重复
          的前缀只留标记，其它失败则整段错误说明作为提示。
        """
        rendered = self._stream_view.rendered_text
        if response.status is TurnStatus.COMPLETED:
            if self._stream_view.had_output:
                self._console.print()  # 正文已流式打完，补分隔空行
            else:
                render_assistant_turn(self._console, response.text)
            return
        notice = response.text
        if rendered and notice.startswith(rendered):
            # 取消轮 response.text = 正文 + "\n" + 标记；去掉已打正文，只追加标记。
            notice = notice[len(rendered) :].lstrip("\n")
        if notice:
            self._console.print(assistant_notice(notice))
        self._console.print()  # 分隔下一轮

    def _handle_slash(self, intent: SlashCommand) -> None:
        spec = self._registry.get(intent.command)
        if spec is None or spec.handler is None:
            self._output.print(f"命令 /{intent.command} 暂未实现。")
            return
        # 由 handler 返回值决定是否落盘：True = 真正写了持久状态。只读命令
        # （/status、/help）返回 False；自己已写专属事件的命令（/plan 写
        # mode_changed）也返回 False，避免同一次操作记两条事件。
        if spec.handler.execute(intent):
            self._session.record_slash_command(intent.command, intent.args)


def _install_sigint(token: CancelToken) -> Callable[[], None]:
    """turn 期间把 Ctrl-C 从「抛 KeyboardInterrupt」改为「置取消 token」。

    prompt_toolkit 的 c-c 绑定只在输入框 app.run() 内生效；分派阶段回到默认
    SIGINT 语义，这里临时接管：handler 只置位 token（协作取消，gateway 在
    chunk 间检查），不抛异常，模型调用因此在下一个检查点安全中止。返回恢复
    函数；非主线程无法安装信号处理器，返回 no-op（测试线程等场景安全降级）。
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    def _cancel(_signum: int, _frame: object) -> None:
        token.cancel()

    previous = signal.signal(signal.SIGINT, _cancel)

    def _restore() -> None:
        signal.signal(signal.SIGINT, previous)

    return _restore
