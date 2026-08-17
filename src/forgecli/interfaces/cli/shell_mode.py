"""人工 Shell 的终端界面 (ADR-0017 §4).

两种形态, 界面**刻意不一样**:

    #                  开一次会话, 打两条边界提示
      进入 Shell 模式 · zsh · cwd=/workspace/ForgeCLI
      输入 exit 或 Ctrl-D 返回 Forge · 环境变量 FORGE_SHELL=1
      <这中间完全是用户和他自己的 Shell, Forge 不参与>
      返回 Forge · shell exit 0

    # clear            跑一条就回来, 成功时**一个字都不打**

一次性命令为什么必须安静: `# clear` 之后再打一行"返回 Forge", 刚清干净的屏幕就又有
东西了 —— 而用户要的正是那块干净屏幕. 这不是省事, 是这条路径成立的前提. 只有非零退出
和启动失败才出声, 因为那时用户需要知道出了什么事.

三条不能加的东西:

- **不做二次确认.** 用户敲 `#` 已经是明确意图, 再问一遍等于把这个入口做成 HITL ——
  而人工 Shell 的全部意义就是它不是 HITL.
- **不伪造 Shell prompt.** 用户看到的必须是他自己 rc 文件里配的那个 prompt, 否则
  "这是我熟悉的终端"这件事就不成立了.
- **不在命令间显示 mode / workspace / 工具状态.** 那是 Agent 侧的东西.

进入提示由 observer 打而不是在 enter() 里打: 提示要说清"起的是哪个 Shell", 而那要等
resolver 解析完才知道; observer.entered 恰好在解析之后, 拿到终端之前.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from rich.console import Console
from rich.markup import escape

from forgecli.application.manual_shell.provider import (
    ManualShellContext,
    ManualShellObserver,
)
from forgecli.application.manual_shell.service import ManualShellService
from forgecli.domain.intents import ManualShellIntent
from forgecli.domain.manual_shell.request import ManualShellRequest
from forgecli.domain.manual_shell.result import ManualShellResult

__all__ = ["ConsoleManualShellObserver", "ShellModeEntry"]

_BOUNDARY = "dim #94e2d5"
_WARN = "yellow"

# POSIX 约定的"命令找不到"退出码; cmd.exe 用 9009.
#
# 只看**退出码**不看输出: §9 不允许捕获 stdout/stderr, 而退出码是个数字, 不是内容.
_NOT_FOUND_CODES = frozenset({127, 9009})

_NOT_FOUND_HINT = '命令未找到. 如果希望ai介入处理, 去掉开头的 "# " 再发一次.'


class ConsoleManualShellObserver(ManualShellObserver):
    """进入会话时的两条边界提示. 一次性命令不打.

    只用 request 里的 Shell 名与 cwd —— 命令, 参数与环境变量值都不打 (§9).
    descriptor 取的是 basename, 不显示完整路径.
    """

    def __init__(self, console: Console) -> None:
        self._console = console

    def entered(self, request: ManualShellRequest) -> None:
        if not request.interactive:
            return
        self._console.print(
            f"\n[{_BOUNDARY}]进入 Shell 模式 · {escape(request.descriptor)}"
            f" · cwd={escape(request.cwd)}[/]"
        )
        # 把 FORGE_SHELL 写进提示, 是因为"我现在到底在不在 Forge 里"这件事光靠一条会
        # 滚走的提示答不了. 变量一直在, 用户可以自己放进 prompt (见 shell_selection).
        self._console.print(
            f"[{_BOUNDARY}]输入 exit 或 Ctrl-D 返回 Forge · 环境变量 FORGE_SHELL=1[/]\n"
        )

    def exited(self, result: ManualShellResult) -> None:
        return None  # 退出摘要由 ShellModeEntry 打, 免得两处都打


class ShellModeEntry:
    """REPL 与 ManualShellService 之间的一层薄壳: 转发, 报结果, 报屏障."""

    def __init__(
        self,
        console: Console,
        service: ManualShellService,
        context_factory: Callable[[], ManualShellContext],
    ) -> None:
        self._console = console
        self._service = service
        # 每次进入现取: cwd 与环境都可能在两次进入之间变了, 而 §6.2 要求每次都从
        # Forge 的 cwd 开始, 不继承上一次 Shell 退出时的目录.
        self._context_factory = context_factory

    def enter(self, intent: ManualShellIntent) -> ManualShellResult:
        # 上下文由工厂现取 (它知道 cwd 与环境), 命令来自意图 (它知道用户敲了什么),
        # 两者在这里合流 —— 工厂每次进入都要跑, 不该为了一个字段去依赖路由结果.
        context = replace(self._context_factory(), command=intent.command)
        result = self._service.enter(intent, context)
        if intent.interactive:
            self._report_session(result)
        else:
            self._report_one_shot(result)
        self._report_barrier()
        return result

    def _report_session(self, result: ManualShellResult) -> None:
        style = _BOUNDARY if result.started else _WARN
        self._console.print(f"[{style}]{escape(result.summary)}[/]")
        # 空行是边界: 子 Shell 的最后一行输出与 Forge 的下一个提示符要分得开.
        # 只有会话打, 一次性命令不打 —— 那会毁掉 `# clear`.
        self._console.print()

    def _report_one_shot(self, result: ManualShellResult) -> None:
        """成功就闭嘴. 只有出事才说话."""
        if not result.started:
            self._console.print(f"[{_WARN}]{escape(result.summary)}[/]")
            return
        if result.signal is not None:
            self._console.print(f"[{_WARN}]命令被信号 {result.signal} 终止[/]")
            return
        if result.exit_code in _NOT_FOUND_CODES:
            # 这条提示专治 `# 命令` 与自然语言的碰撞: 单行 `# 标题` 会被当成命令,
            # 而这里把"莫名其妙报错"变成"哦我该去掉那个井号".
            self._console.print(f"[{_WARN}]{_NOT_FOUND_HINT}[/]")
            return
        if result.exit_code:
            # 非零退出照实说, 但不加解释 —— 那是用户自己的命令, 他比 Forge 清楚.
            self._console.print(f"[{_WARN}]命令退出码 {result.exit_code}[/]")

    def _report_barrier(self) -> None:
        """失效屏障失败必须当场说, 而且要说清后果 (§12).

        不能只记日志: 下一次 Agent turn 会被拒, 用户得知道为什么, 否则看到的是
        "Forge 突然不干活了".
        """
        barrier = self._service.barrier
        if barrier.blocked:
            self._console.print(f"[red]{escape(barrier.block_reason)}[/]")
