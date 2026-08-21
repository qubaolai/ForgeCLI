"""无围栏 Provider (ADR-0030 决策 1 / 5).

它存在的意义只有一个: **不留宿主直连旁路**. 没有围栏时也要走同一个接缝, 否则代码里
就会出现"有围栏走这条, 没围栏走那条"的隐藏分支, 而那种分支迟早会被绕过.

它不是 Null 实现: 原生 Windows 上它是唯一可用的 Provider, 而 ADR-0030 决策 5 为这种
状态定义了明确的行为 —— 工作区内照常, 跨工作区与网络一律 ASK.
"""

from __future__ import annotations

from forgecli.application.tools.sandbox_provider import SandboxProvider, SelfTestReport
from forgecli.domain.execution.fence import FencePolicy

__all__ = ["NoSandboxProvider"]


class NoSandboxProvider(SandboxProvider):
    @property
    def name(self) -> str:
        return "none"

    def available(self) -> bool:
        return True

    def wrap(self, argv: tuple[str, ...], policy: FencePolicy) -> tuple[str, ...]:
        if not argv:
            raise OSError("argv 为空")
        return argv

    def self_test(self) -> SelfTestReport:
        """三项全 False. 如实报告"什么都拦不住", 不假装.

        `confined` 因此恒为 False, 画像落到 UNCONFINED, 裁决层据此收紧.
        """
        return SelfTestReport(provider=self.name, failures=("没有平台围栏",))
