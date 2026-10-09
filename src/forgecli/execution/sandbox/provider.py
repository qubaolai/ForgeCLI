"""SandboxProvider: 把一条 argv 包进平台围栏.

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
from collections.abc import Mapping
from dataclasses import dataclass

from forgecli.core.execution.fence import FencePolicy

__all__ = ["SandboxProvider", "SelfTestReport", "proxy_environment"]

# 子进程要走代理时给它的变量 (对应 sandbox-runtime 的 generateProxyEnvVars). NO_PROXY 把
# 本机与私有网段排除在外: 这些地址围栏本来就不放 (除非 allowLocalBinding), 走代理反而
# 让代理去连它们.
_NO_PROXY = (
    "localhost,127.0.0.1,::1,169.254.0.0/16,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
)


def proxy_environment(proxy_url: str) -> dict[str, str]:
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "ALL_PROXY": proxy_url,
        "all_proxy": proxy_url,
        "NO_PROXY": _NO_PROXY,
        "no_proxy": _NO_PROXY,
    }


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
    """平台沙箱实现. 这里定义抽象类 分不同系统进行实现"""

    @property
    @abstractmethod
    def name(self) -> str:
        """进 ExecutionProfile 的标识."""

    @abstractmethod
    def available(self) -> bool:
        """本机能不能用这个 Provider. 必须实测, 不能只看 platform.system()."""

    @abstractmethod
    def wrap(self, argv: tuple[str, ...], policy: FencePolicy) -> tuple[str, ...]:
        """把 argv 包进沙箱. 包不了就抛 OSError, 由调用方 fail closed."""

    @abstractmethod
    def environment_for(self, policy: FencePolicy) -> Mapping[str, str]:
        """沙箱内子进程要覆盖的环境变量: 临时目录, 代理地址. 沙箱没有网络出口就没有
        代理变量."""

    @abstractmethod
    def self_test(self) -> SelfTestReport:
        """真的去做三次被禁止的操作, 确认它们确实失败."""
