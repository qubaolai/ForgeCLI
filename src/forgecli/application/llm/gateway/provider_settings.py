"""provider 运行时设置只读视图（ADR-0011 §5 / §7 / §16）。

gateway 发起调用前需要 provider 级的三项运行时参数：默认超时、重试上限与
credential reference 列表。它们来自应用级 `[llm.providers.*]` 配置，但 gateway
不解析 TOML（§4）——由 `ProviderSettingsSource` 端口提供现读视图，infrastructure
侧实现从 LlmConfigService 读取（interfaces 装配时注入）。

参数合并优先级（§5）：请求级 ModelParams / timeout > 按 origin 系统默认 >
provider 默认（timeout、max_retries）。本模块只承载 provider 默认这一层。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderRuntimeSettings:
    """一家 provider 的运行时调用参数（只读）。"""

    provider_id: str
    timeout_seconds: float
    max_retries: int
    # 有序 credential reference（如 "DEEPSEEK_API_KEY"）；空表示 keyless。
    credential_refs: tuple[str, ...] = ()
    # 429 短等重试阈值（ADR-0012 §2）：retry_after <= 阈值时本地等待后同凭证重试，
    # 超过阈值则冷却换下一凭证。来自应用级 [llm.retry]。
    wait_threshold_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("ProviderRuntimeSettings.provider_id 不能为空")
        if self.timeout_seconds <= 0:
            raise ValueError("ProviderRuntimeSettings.timeout_seconds 必须为正数")
        if self.max_retries < 0:
            raise ValueError("ProviderRuntimeSettings.max_retries 不能为负")
        if self.wait_threshold_seconds < 0:
            raise ValueError("ProviderRuntimeSettings.wait_threshold_seconds 不能为负")


class ProviderSettingsSource(ABC):
    """provider 运行时设置的现读端口；每次调用现读以反映会话中配置变更。"""

    @abstractmethod
    def settings_for(self, provider_id: str) -> ProviderRuntimeSettings:
        """返回 provider 的运行时设置；未知 provider 抛 UnknownProvider。"""
