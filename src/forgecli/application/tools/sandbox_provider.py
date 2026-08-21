"""SandboxProvider: 把一条 argv 包进平台围栏 (ADR-0030 决策 1 / 3).

Provider 只做一件事: 接受原始 argv 与一份 `FencePolicy`, 返回被围栏包住的 argv.
它**不认识任何命令** —— 包 `ls` 和包 `rm -rf /` 走的是同一段代码, 差别由内核在系统
调用那一刻给出.

能力必须行为自测, 不能从配置里读出来然后声称拥有. `self_test` 会真的去写一个边界外的
文件, 真的去读一个受保护路径, 真的去连一个外部地址, 三项全部确认失败才算 `confined`.
任意一项不过就是 UNCONFINED —— 不存在"部分隔离"这一档.

**不回退到更弱 Provider.** 选定的 Provider 在运行期失效时返回结构化失败, 不静默换一个
能跑起来的. 沙箱最经典的事故就是静默降级.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from forgecli.domain.execution.fence import FencePolicy

__all__ = ["SandboxProvider", "SelfTestReport"]


@dataclass(frozen=True)
class SelfTestReport:
    """一次行为自测的结果. 三项全过才算围住了."""

    provider: str
    outside_write_blocked: bool = False
    protected_read_blocked: bool = False
    network_blocked: bool = False
    failures: tuple[str, ...] = ()

    @property
    def confined(self) -> bool:
        return (
            self.outside_write_blocked
            and self.protected_read_blocked
            and self.network_blocked
        )


class SandboxProvider(ABC):
    """一个平台围栏实现."""

    @property
    @abstractmethod
    def name(self) -> str:
        """进 ExecutionProfile 与审计的标识."""

    @abstractmethod
    def available(self) -> bool:
        """本机能不能用这个 Provider. 必须实测, 不能只看 platform.system()."""

    @abstractmethod
    def wrap(self, argv: tuple[str, ...], policy: FencePolicy) -> tuple[str, ...]:
        """把 argv 包进围栏. 包不了就抛 OSError, 由调用方 fail closed."""

    @abstractmethod
    def self_test(self) -> SelfTestReport:
        """真的去做三次被禁止的操作, 确认它们确实失败."""
