"""ManualShellService: 人工 Shell 的编排 (ADR-0017 §5).

它做的事很少, 但每一件都是边界:

1. 校验来源. 只接 ``ManualShellIntent`` —— 而那个值对象自己就拒绝非 TTY_USER 构造.
2. 校验状态. 只有 PROMPT_IDLE 能进 (§3). 不允许"暂停一半 Agent turn 再进 Shell".
3. 拿终端租约, 调 Provider, 在 ``finally`` 里还终端.
4. 无论怎么结束都触发失效屏障.

它**不做**的事同样是边界 (§5): 不解析 Shell 内命令, 不捕获 stdout/stderr, 不做安全裁决,
不写工具审计, 不把任何 Shell 内容变成 conversation message.

本模块不 import Agent, 工具与安全的任何东西. 这不是风格问题: 只要这里能拿到
``ToolRequestCoordinator``, "人工 Shell 顺便记一条 learned allow rule" 这种看起来很贴心
的改动就会有人做, 而它等于让用户手敲的命令替 Agent 拿到授权 (§2).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import Enum

from forgecli.application.manual_shell.mutation_barrier import ManualMutationBarrier
from forgecli.application.manual_shell.provider import (
    InteractiveShellProvider,
    InteractiveShellResolver,
    ManualShellContext,
    ManualShellObserver,
    ManualShellUnavailable,
    TerminalLease,
)
from forgecli.domain.intents import ManualShellIntent
from forgecli.domain.manual_shell.result import ManualShellResult

__all__ = ["ManualShellPhase", "ManualShellService"]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ManualShellPhase(Enum):
    """§3 的状态机. 值会进诊断输出, 不要改."""

    PROMPT_IDLE = "prompt_idle"
    SHELL_PREPARING = "shell_preparing"
    SHELL_ACTIVE = "shell_active"
    SHELL_RESTORING = "shell_restoring"


class ManualShellService:
    def __init__(
        self,
        resolver: InteractiveShellResolver,
        provider: InteractiveShellProvider,
        lease: TerminalLease,
        observer: ManualShellObserver,
        *,
        barrier: ManualMutationBarrier | None = None,
        clock: Callable[[], str] = _now,
    ) -> None:
        """observer 是**必填**的.

        ADR §4 与 §6.2 把进入提示定成硬性要求 (尤其是那行 cwd —— 用户会误以为继承了
        上一次人工 Shell 的目录), 而那行字是 observer 打的. 给它一个默认值就意味着漏接
        的后果是"界面少一行"这种没人会注意到的静默降级. 真不想要通知的调用方显式传
        NullManualShellObserver.
        """
        self._resolver = resolver
        self._provider = provider
        self._lease = lease
        self._barrier = barrier or ManualMutationBarrier()
        self._observer = observer
        self._clock = clock
        self._phase = ManualShellPhase.PROMPT_IDLE

    @property
    def phase(self) -> ManualShellPhase:
        return self._phase

    @property
    def barrier(self) -> ManualMutationBarrier:
        return self._barrier

    def enter(
        self, intent: ManualShellIntent, context: ManualShellContext
    ) -> ManualShellResult:
        """进入人工 Shell, 阻塞到它结束.

        只接 intent 不接裸字符串: 意图对象的构造函数已经保证了 origin 是 TTY_USER,
        换成字符串参数等于把那道校验丢掉.
        """
        del intent  # 类型本身就是凭据, 不需要再读它的字段
        if self._phase is not ManualShellPhase.PROMPT_IDLE:
            raise ManualShellUnavailable(
                f"当前不在空闲提示符 (phase={self._phase.value}), 无法进入 Shell 模式"
            )
        started = self._clock()
        self._phase = ManualShellPhase.SHELL_PREPARING
        try:
            return self._run(context, started)
        finally:
            # 屏障在 finally 里: 启动失败的 Shell 也可能已经跑过 rc 文件, "没进去"
            # 不等于"什么都没发生" (§10).
            self._phase = ManualShellPhase.SHELL_RESTORING
            self._barrier.trip()
            self._phase = ManualShellPhase.PROMPT_IDLE

    def _run(self, context: ManualShellContext, started: str) -> ManualShellResult:
        try:
            request = self._resolver.resolve(context)
        except ManualShellUnavailable as exc:
            return self._failed(started, str(exc))

        self._observer.entered(request)
        try:
            with self._lease.acquire():
                self._phase = ManualShellPhase.SHELL_ACTIVE
                result = self._provider.open(request)
        except ManualShellUnavailable as exc:
            result = self._failed(started, str(exc))
        self._observer.exited(result)
        return result

    def _failed(self, started: str, reason: str) -> ManualShellResult:
        return ManualShellResult(
            started_at=started, finished_at=self._clock(), start_error=reason
        )
