"""provider 运行时设置只读视图（ADR-0011 §5 / §7 / §16）。

gateway 发起调用前需要 provider 级的三项运行时参数：默认超时、重试上限与
credential reference 列表。它们来自应用级 `[llm.providers.*]` 配置，但 gateway
不解析 TOML（§4）——由 `LlmConfigProviderSettingsSource` 端口提供现读视图，infrastructure
侧实现从 LlmConfigService 读取（interfaces 装配时注入）。

参数合并优先级（§5）：请求级 ModelParams / timeout > 按 origin 系统默认 >
provider 默认（timeout、max_retries）。本模块只承载 provider 默认这一层。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.llm import providers as provider_registry
from forgecli.llm.config_service import LlmConfigService


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
    # 配额耗尽的冷却时长 (ADR-0059): 同样来自应用级 [llm.retry], 所有 provider 共用.
    quota_cooldown_seconds: float = 3600.0

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("ProviderRuntimeSettings.provider_id 不能为空")
        if self.timeout_seconds <= 0:
            raise ValueError("ProviderRuntimeSettings.timeout_seconds 必须为正数")
        if self.max_retries < 0:
            raise ValueError("ProviderRuntimeSettings.max_retries 不能为负")
        if self.wait_threshold_seconds < 0:
            raise ValueError("ProviderRuntimeSettings.wait_threshold_seconds 不能为负")
        if self.quota_cooldown_seconds < 0:
            raise ValueError("ProviderRuntimeSettings.quota_cooldown_seconds 不能为负")


# ---- 原 infrastructure/llm/settings.py ----
# LlmConfigProviderSettingsSource 的 LlmConfigService 实现（ADR-0011 §5 / §7）。
#
# 每次 settings_for 都现读 LlmConfigService（会话中 /config 修改立即生效）：
#     - timeout / max_retries：取 provider 配置（服务解析时已填默认 60s / 2 次）。
#     - credential_refs：显式 `credential_refs` 数组优先；缺省时用 api_key_env
#       作为单条引用；api_key_env 也为空（如 local）→ keyless（空元组）。credential
#       reference 即环境变量名，不带前缀（2026-07-17 复核，见 ADR-0011 §7）。
#     - wait_threshold_seconds / quota_cooldown_seconds：应用级 [llm.retry]
#       （ADR-0012 §2, ADR-0059），所有 provider 共用。
#
# 明文 key 永不出现在这里——本模块只搬运环境变量名。
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_RETRIES = 2


class LlmConfigProviderSettingsSource:
    """从 LlmConfigService + 封闭注册表现读 provider 运行时设置。"""

    def __init__(self, llm_config_service: LlmConfigService) -> None:
        self._llm = llm_config_service

    def settings_for(self, provider_id: str) -> ProviderRuntimeSettings:
        """返回 provider 的运行时设置；未知 provider 抛 UnknownProvider。"""
        # 先读配置再查注册表: 用户自建的供应商是读配置那一刻登记进注册表的, 顺序反了
        # 会让一家配好的供应商在本进程第一次读配置之前被判成"未知".
        provider = self._llm.config().provider(provider_id)
        spec = provider_registry.require_known_provider(provider_id)
        retry = self._llm.retry_settings()
        refs: tuple[str, ...]
        if provider is None:
            # 未配置 provider 段：用注册表默认（api_key_env 即环境变量名引用）。
            refs = (spec.api_key_env,) if spec.api_key_env else ()
            return ProviderRuntimeSettings(
                provider_id=provider_id,
                timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                max_retries=_DEFAULT_MAX_RETRIES,
                credential_refs=refs,
                wait_threshold_seconds=retry.wait_threshold_seconds,
                quota_cooldown_seconds=retry.quota_cooldown_seconds,
            )
        if provider.credential_refs:
            refs = provider.credential_refs
        elif provider.api_key_env:
            refs = (provider.api_key_env,)
        else:
            refs = ()
        return ProviderRuntimeSettings(
            provider_id=provider_id,
            timeout_seconds=float(provider.timeout),
            max_retries=provider.max_retries,
            credential_refs=refs,
            wait_threshold_seconds=retry.wait_threshold_seconds,
            quota_cooldown_seconds=retry.quota_cooldown_seconds,
        )
