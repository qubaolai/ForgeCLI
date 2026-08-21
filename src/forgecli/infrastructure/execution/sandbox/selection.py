"""按平台挑一个 Provider, 并用行为自测确认它真的围得住 (ADR-0030 决策 3).

**不回退到更弱 Provider**: 候选按平台唯一确定, 自测不过就如实降级为 UNCONFINED, 而不是
换一个能跑起来的接着试. 静默降级是沙箱最经典的事故形态.
"""

from __future__ import annotations

import platform

from forgecli.application.tools.sandbox_provider import SandboxProvider, SelfTestReport
from forgecli.infrastructure.execution.sandbox.bubblewrap import BubblewrapProvider
from forgecli.infrastructure.execution.sandbox.none import NoSandboxProvider
from forgecli.infrastructure.execution.sandbox.seatbelt import SeatbeltProvider

__all__ = ["select_provider"]


def select_provider(
    system: str | None = None,
) -> tuple[SandboxProvider, SelfTestReport]:
    """返回 (选定的 Provider, 它的自测报告).

    报告里的 `confined` 决定 `IsolationLevel`. 调用方必须用报告而不是 Provider 的名字
    去判断有没有围栏 —— 一个装了 bwrap 但 userns 被关掉的机器上, 名字是对的, 围栏是
    不存在的.
    """
    name = (system or platform.system()).casefold()
    candidate: SandboxProvider
    if name == "darwin":
        candidate = SeatbeltProvider()
    elif name == "linux":
        candidate = BubblewrapProvider()
    else:
        candidate = NoSandboxProvider()

    if not candidate.available():
        fallback = NoSandboxProvider()
        return fallback, fallback.self_test()
    report = candidate.self_test()
    if not report.confined:
        # 自测不过: 用回 NoSandbox, 让上层看到的名字与实际能力一致. 这不是"回退到更弱
        # Provider"—— 那条禁令说的是同一次授权内换 Provider 接着跑; 这里是启动期探测,
        # 结论是"本机没有可用围栏".
        fallback = NoSandboxProvider()
        return fallback, SelfTestReport(
            provider=fallback.name,
            failures=(f"{candidate.name} 自测未通过: {', '.join(report.failures)}",),
        )
    return candidate, report
