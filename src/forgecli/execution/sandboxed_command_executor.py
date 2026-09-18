"""把围栏包在 LocalCommandExecutor 外面 (ADR-0030 决策 1).

装饰器而不是分支: `CommandExecutor` 的实现里没有一处 `if 有围栏`. 有没有围栏体现在
组合根装配了哪个 Provider, 不体现在执行路径上.

**fail closed**: 真实 Provider 拿到一个不带 `fence` 的请求会直接失败, 而不是"没策略
就不围". 漏传策略是 bug, 而这个 bug 的后果是一次完全没有围栏的执行 —— 那正是必须
响的地方.

围栏内的环境由 Provider 定 (`environment_for`): TMPDIR 指向实例临时目录, 代理变量指向
本机代理. 叠在净化过的环境之上, 盖掉同名变量.
"""

from __future__ import annotations

from dataclasses import replace

from forgecli.common.cancellation import CancelToken
from forgecli.execution.command_executor import (
    CommandExecutor,
    CommandOutcome,
    CommandRequest,
)
from forgecli.execution.sandbox.provider import SandboxProvider

__all__ = ["SandboxedCommandExecutor"]


class SandboxedCommandExecutor(CommandExecutor):
    def __init__(self, inner: CommandExecutor, provider: SandboxProvider) -> None:
        self._inner = inner
        self._provider = provider

    def run(
        self, request: CommandRequest, cancel: CancelToken | None = None
    ) -> CommandOutcome:
        if request.fence is None:
            return CommandOutcome(
                exit_code=None,
                failure=(
                    f"围栏策略缺失, 拒绝在 {self._provider.name} 下执行: "
                    "这是装配错误, 不是可以放行的情形"
                ),
            )
        try:
            argv = self._provider.wrap(request.argv, request.fence)
        except OSError as exc:
            return CommandOutcome(exit_code=None, failure=f"围栏创建失败: {exc}")
        environment = {
            **request.environment,
            **self._provider.environment_for(request.fence),
        }
        return self._inner.run(
            replace(request, argv=argv, environment=environment), cancel
        )
