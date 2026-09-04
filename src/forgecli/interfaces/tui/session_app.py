"""终端交互式会话的主循环 (ADR-0045).

一条硬约束贯穿整个文件: **只有主线程往终端写**. turn 跑在 ``ProjectRuntime`` 起的后台
线程里, 事件由那个线程产生, 但渲染必须回到这里 —— 两个线程同时 print 会把流式正文和
审批卡片搅在一起, 而审批卡片正是用户要读着做决定的东西.

主线程因此是"轮询 + 画"而不是"阻塞等结果": 它要一边把新事件画出来, 一边看审批 broker
有没有人在等, 一边接住 Ctrl-C. 阻塞在任何一件事上, 另外两件就都停摆.
"""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console
from rich.text import Text

from forgecli.domain.intents import (
    MODE_PRESETS,
    PRESET_NAMES,
    InputOrigin,
    stance_label,
)
from forgecli.infrastructure.config import config_dir
from forgecli.interfaces.exit_codes import ExitCode
from forgecli.interfaces.runtime.project_runtime import (
    ProjectRuntime,
    ProjectRuntimeRegistry,
)
from forgecli.interfaces.tui import banner
from forgecli.interfaces.tui.approval_prompt import ask_decision, render_card
from forgecli.interfaces.tui.commands.context import CommandContext, NoActiveProject
from forgecli.interfaces.tui.commands.registry import COMMANDS, dispatch
from forgecli.interfaces.tui.console import (
    STYLE_ACCENT,
    STYLE_DIM,
    error,
    warn,
)
from forgecli.interfaces.tui.plan_review import review
from forgecli.interfaces.tui.prompt import ForgePrompt
from forgecli.interfaces.tui.run_view import RunEventCollector, TerminalRunView
from forgecli.interfaces.tui.session_exit import SessionExit
from forgecli.shared import __version__

# 轮询间隔. 够快到流式正文看不出卡顿, 又不至于让一个空闲的等待烧掉一个核.
_POLL_SECONDS = 0.04


class SessionApp:
    """一个终端会话. 项目可以在会话中途切换, 事件订阅跟着换."""

    def __init__(self, console: Console, registry: ProjectRuntimeRegistry) -> None:
        self.console = console
        self.registry = registry
        self.context = CommandContext(console=console, registry=registry)
        # 输入框只要命令名与一句说明, 用来画 "/" 菜单; 执行仍由 registry 分派.
        self.prompt = ForgePrompt(
            [(item.name.removeprefix("/"), item.summary) for item in COMMANDS],
            status_provider=self._runtime_status,
            on_mode_step=self._step_mode,
            history_file=_history_file(),
        )
        self._bound: ProjectRuntime | None = None
        self._collector = RunEventCollector()

    # ---- 主循环 ----

    def run(self) -> int:
        self._banner()
        while not self.context.exit_requested:
            self._bind_active()
            try:
                line = self.prompt.read()
            except SessionExit:
                # 空行连按两次 Ctrl-C. 输入框自己判的三态退出, 这里只负责收尾.
                break
            except EOFError:
                # 输入流走到尽头 (stdin 被关掉). 空行 Ctrl-D 不走这里 —— 输入框把它
                # 消费掉了, 退出只认 Ctrl-C x2 或 /exit.
                break
            text = line.strip()
            if not text:
                continue
            # 输入框提交后会把自己擦掉 (erase_when_done), 用户那一句不会留在滚动历史
            # 里. 这里补回显一次, 否则翻上去看只有模型在自言自语.
            self.console.print(Text(f"› {text}", style=STYLE_ACCENT), highlight=False)
            try:
                if text.startswith("/"):
                    dispatch(self.context, text)
                else:
                    self._turn(text)
            except NoActiveProject as exc:
                error(self.console, str(exc))
            except KeyboardInterrupt:
                # 命令里的菜单也停在读键上. 在那里按 Ctrl-C 是"这一步不做了",
                # 不是"退出 Forge" —— 让它冒到这里之外, 一次选错菜单就会把整个会话
                # (连同还没提交的项目激活状态) 一起带走.
                self.console.print()
                self.console.print(Text("已取消", style=STYLE_DIM))
            except EOFError:
                # 菜单里按 Ctrl-D 同理: 读一行的回退路径抛的是 EOFError.
                self.console.print()
                self.console.print(Text("已取消", style=STYLE_DIM))
        self.console.print(Text("再见.", style=STYLE_DIM))
        return ExitCode.OK

    # ---- 输入框要的两个回调 ----

    def _runtime_status(self) -> str:
        """输入框右下角: 当前模式, 模型与它的 thinking. 每次重绘现读.

        模式必须在这里 —— Tab / Shift-Tab 切档的**唯一**视觉反馈就是这一行. 少了它,
        那个快捷键按下去屏幕上什么都不会变, 而它改的是 Forge 能不动手做什么.

        现读而不是缓存: /model 或 /thinking 改完, 下一帧就该反映出来.
        """
        runtime = self.registry.active
        if runtime is None:
            return ""
        parts = [stance_label(runtime.session.current().mode)]
        model = runtime.current_model()
        if model is None:
            parts.append("未设置模型")
            return " · ".join(parts)
        parts.append(str(model))
        thinking = runtime.thinking_view()
        if thinking.get("configured") and thinking.get("mode") == "on":
            effort = str(thinking.get("effort", "") or "")
            parts.append(f"thinking {effort}".strip())
        return " · ".join(parts)

    def _step_mode(self, step: int) -> None:
        """Tab / Shift-Tab: 沿预设的权限梯度切一档.

        两端截断不回绕: 从最松的一档再按一次 Tab 跳回最紧的, 是那种按下去才发现的
        意外 —— 而这一轴上的每一步都在改 Forge 能不动手做什么。
        """
        runtime = self.registry.active
        if runtime is None or runtime.busy:
            return
        names = [preset.value for preset in MODE_PRESETS]
        current = runtime.session.current().mode
        index = next(
            (
                position
                for position, name in enumerate(names)
                if PRESET_NAMES[name] == current
            ),
            # 当前是两个轴的自由组合, 不在预设里. 从哪一端起步跟着方向走, 免得
            # "按一下 Tab 反而更严了"。
            -1 if step > 0 else len(names),
        )
        target = max(0, min(index + step, len(names) - 1))
        if PRESET_NAMES[names[target]] != current:
            runtime.set_mode(PRESET_NAMES[names[target]])

    def _banner(self) -> None:
        runtime = self.registry.active
        rows = [("版本", __version__)]
        if runtime is None:
            rows.append(("项目", "尚未选择, 用 /projects 打开一个"))
        else:
            model = runtime.current_model()
            rows.extend(
                [
                    ("工作区", runtime.project.primary_workspace_root),
                    ("模式", stance_label(runtime.session.current().mode)),
                    (
                        "模型",
                        str(model) if model is not None else "未设置, 用 /model 选",
                    ),
                ]
            )
        self.console.print()
        banner.render(self.console, rows)
        self.console.print(
            Text(
                "/help 看全部命令; /<命令> -h 看某一条; 直接说话就是和模型对话.",
                style=STYLE_DIM,
            )
        )

    def _bind_active(self) -> None:
        """项目换了就换一份事件订阅.

        订阅挂在 ``event_bus`` 上, 而总线随 ``ProjectRuntime`` 一起换. 不重新挂的话,
        切完项目之后终端一个事件都收不到 —— 而 turn 照样在跑.
        """
        active = self.registry.active
        if active is self._bound:
            return
        self._bound = active
        self._collector = RunEventCollector()
        if active is not None:
            active.event_bus.subscribe(self._collector)

    # ---- 一轮 ----

    def _turn(self, text: str) -> None:
        runtime = self.context.runtime
        try:
            runtime.start_turn(text, origin=InputOrigin.CLI_USER)
        except (RuntimeError, ValueError) as exc:
            error(self.console, str(exc))
            return
        self._drive(runtime)
        self._settle(runtime)

    def _drive(self, runtime: ProjectRuntime) -> None:
        """把这一轮跑完: 画事件, 接审批, 接 Ctrl-C."""
        view = TerminalRunView(self.console)
        stopping = False
        while True:
            try:
                for event in self._collector.drain():
                    view.handle(event)
                pending = runtime.approvals.list_pending()
                if pending:
                    self._resolve_approval(runtime, pending[0])
                    continue
                run = runtime.current_run()
                if run is None or run.status != "running":
                    break
                time.sleep(_POLL_SECONDS)
            except KeyboardInterrupt:
                self.console.print()
                if stopping:
                    # 第二次 Ctrl-C: 不再等这一轮自己收尾. 后台线程是 daemon, 但它
                    # 手上可能还有一个跑着的子进程 —— 所以要说清楚它没有被杀掉.
                    warn(self.console, "不再等这一轮收尾; 已发出的命令可能仍在跑")
                    break
                stopping = True
                runtime.cancel()
                warn(self.console, "正在停止这一轮…")
        for event in self._collector.drain():
            view.handle(event)

    def _resolve_approval(
        self, runtime: ProjectRuntime, pending: dict[str, object]
    ) -> None:
        approval_id = str(pending.get("approval_id", ""))
        view = pending.get("view")
        if not isinstance(view, dict):
            return
        render_card(self.console, view, mandatory=bool(pending.get("mandatory")))
        decision = ask_decision(self.console, view)
        if decision is None:
            # Ctrl-C 不等于拒绝: 那是"停止这一轮". 判成拒绝, 模型会收到一条用户从来
            # 没说过的拒绝, 并据此往下走.
            runtime.cancel()
            warn(self.console, "正在停止这一轮…")
            return
        if not runtime.approvals.resolve(approval_id, decision):
            error(self.console, "这条审批已经不在等待中了")

    def _settle(self, runtime: ProjectRuntime) -> None:
        """收尾: 失败要说出来, 停在计划评审要把人接住."""
        while True:
            run = runtime.current_run()
            if run is None:
                return
            if run.status == "failed":
                # 只在有异常串时才多打一行: 正常的失败终态已经由 TURN_FAILED 那一行
                # 带着原因报过, 再报一次"本轮失败"是把同一件事说两遍.
                if run.error:
                    error(self.console, run.error)
                return
            if run.status != "waiting_plan_review":
                return
            if not review(self.console, runtime):
                return
            if not runtime.busy:
                return
            # "同意并执行"会以本轮的名义再起一轮 (那句话是程序给的, 不是用户又说了一遍).
            self._drive(runtime)


def _history_file() -> Path:
    """输入历史与其他用户级状态放在一起, 不落进工作区."""
    return config_dir() / "cli-history"
