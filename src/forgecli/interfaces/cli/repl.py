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
from forgecli.application.intent_router import IntentRouter
from forgecli.application.session import SessionService
from forgecli.application.slash_commands import CommandRegistry
from forgecli.domain.conversation.turn import (
    AssistantResponse,
    TurnPause,
    TurnStatus,
)
from forgecli.domain.intents import (
    InputOrigin,
    ManualShellIntent,
    SlashCommand,
    UnknownCommand,
    UserIntent,
    UserMessage,
)
from forgecli.domain.session.events import EventType
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.plan_review_prompt import PlanReviewPrompt
from forgecli.interfaces.cli.prompt_loop import ForgePrompt
from forgecli.interfaces.cli.run_renderer import TerminalRunRenderer
from forgecli.interfaces.cli.session_exit import SessionExit
from forgecli.interfaces.cli.shell_mode import ShellModeEntry
from forgecli.interfaces.cli.transcript import (
    assistant_notice,
    render_assistant_turn,
    render_user_turn,
)
from forgecli.interfaces.cli.tty.tty import stdin_is_tty
from forgecli.shared.cancellation import CancelToken

# 一次评审最多起一轮后续 turn, 而那一轮可能又停在评审. 没有上限的话, 一个每轮都提
# 计划的模型能造出一个无人输入的死循环 (ADR-0023 决策 6).
_MAX_REVIEW_CHAIN = 3


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
        stream_view: TerminalRunRenderer,
        cancel_source: TurnCancelSource,
        prompt_status: Callable[[], str] | None = None,
        shell_mode: ShellModeEntry | None = None,
        plan_review: PlanReviewPrompt | None = None,
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
        self._shell_mode = shell_mode
        # 缺省 None: 没装配评审界面时计划照常落盘并保持 proposed, 链路不断.
        self._plan_review = plan_review

    def run(self) -> None:
        # banner 由 bootstrap 在信任解析前渲染；这里只给进入会话的提示。
        self._console.print(
            Panel.fit(
                "进入 Forge 交互式会话。\n"
                "输入 [bold]/help[/] 查看命令，"
                "[bold]/exit[/] 或空行连按两次 [bold]Ctrl-C[/] 退出。",
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
                # 读取与分派共用一个 except: SessionExit 有两个抛出点——输入框的
                # Ctrl-C×2 和 /exit 命令——两者都要退出这同一个循环。
                self._process_line(prompt.read())
            except SessionExit:
                # 主动退出，不向终端暴露 traceback。
                break
            except EOFError:
                # 输入流结束（stdin 被关闭等）：已经读不到下一行，安全收尾。
                # 空行 Ctrl-D 不走这里——prompt 的 c-d 绑定消费掉了它（退出只认
                # Ctrl-C×2 或 /exit，ADR-0007），所以这里不是退出快捷键，
                # 只是流末尾兜底。
                break

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
        """清洗一行输入并分派：空行忽略、退出词退出、其余交给 IntentRouter。

        `origin=TTY_USER` **只在这里**标注，而这里是 Forge 里唯一从前台真终端读到这一行
        的地方（run() 开头已拒绝非 TTY）。人工 Shell 的特权来自这个来源标记，不是 `#`
        这个字符（ADR-0017 §2）——模型文本、工具输出、事件重放都走不到这一行。
        """
        text = line.strip()
        if not text:
            return
        self._dispatch(self._router.route(text, origin=InputOrigin.TTY_USER))

    def _dispatch(self, intent: UserIntent) -> None:
        match intent:
            case ManualShellIntent():
                self._enter_shell_mode(intent)
            case UserMessage():
                # 同屏显示一轮对话：先回显用户输入(绿)，再给助手输出(青绿)。
                # 处理期间流式正文 append-only 逐行提交；收尾按终态追加提示。
                render_user_turn(self._console, intent.text)
                self._run_conversation(intent.text)
            case SlashCommand():
                self._handle_slash(intent)
            case UnknownCommand():
                self._output.print(intent.error_message)
            case _:
                self._output.print("无法处理的输入。")

    def _run_conversation(self, text: str) -> None:
        """跑一轮, 渲染, 必要时驱动评审并以合成文本再跑一轮 (ADR-0023).

        再入**不经过 _process_line**: 那里会把文本按 TTY 用户输入解析, 于是一段以 `#`
        开头的补充意见就会变成一次不受裁决的 Shell. 合成文本必须直接进 agent turn,
        它天然带 InputOrigin.PROGRAM.

        链上限存在的理由: 一次评审最多起一轮后续 turn, 而那一轮可能又停在评审. 一个每轮
        都提计划的模型能造出无人输入的死循环.
        """
        for _ in range(_MAX_REVIEW_CHAIN):
            response = self._run_turn(text)
            self._finish_render(response)
            if response.pause is not TurnPause.PLAN_REVIEW:
                return
            follow_up = self._review_plan()
            if not follow_up:
                return
            text = follow_up
        self._output.print(
            "连续多轮都停在计划评审, 已回到提示符. 直接说你想怎么做会更快."
        )

    def _review_plan(self) -> str:
        """驱动一次评审, 返回要再跑一轮的文本 (空表示到此为止).

        必须在 _run_turn 之后调用: 那里的 Rich Live 已经退出, 而同一个 console 上两个
        Live 会打架.
        """
        if self._plan_review is None:
            self._output.print("计划已保存, 但当前未装配评审界面.")
            return ""
        mode = self._session.current().mode
        outcome = self._plan_review.run(mode)
        if outcome is None:
            # 中断不是裁决: 什么都没发生, 也就没有可审计的决定.
            return ""
        if outcome.plan is not None:
            # 人怎么裁的要进审计. 与 PLAN_CREATED 一样只记摘要 —— 补充意见的原文进的是
            # 下一轮的 user_message, 这里再存一份就是两个会漂的副本.
            self._session.record_tool_event(
                EventType.PLAN_REVIEWED,
                {
                    "plan_id": outcome.plan.plan_id,
                    "revision": outcome.plan.revision,
                    "decision": outcome.choice.value,
                    "upgraded_mode": (
                        ""
                        if outcome.upgraded_mode is None
                        else outcome.upgraded_mode.value
                    ),
                },
            )
        if outcome.upgraded_mode is not None:
            # 等价于用户手敲 /accept-edits, 经 SessionService.set_mode, 不新增旁路.
            self._session.set_mode(outcome.upgraded_mode)
        return outcome.follow_up

    def _enter_shell_mode(self, intent: ManualShellIntent) -> None:
        """把终端交给用户自己的 Shell。

        没装配 shell_mode 时给一条明确说明而不是静默把 `#` 当自然语言发给模型：后者会
        让用户以为 Forge 没听懂，然后再敲一次。
        """
        if self._shell_mode is None:
            self._output.print("当前未装配人工 Shell 模式，`#` 暂不可用。")
            return
        self._shell_mode.enter(intent)

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
