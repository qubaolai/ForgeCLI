"""ProviderSettingsSource 的 LlmConfigService 实现（ADR-0011 §5 / §7）。

每次 settings_for 都现读 LlmConfigService（会话中 /config 修改立即生效）：
    - timeout / max_retries：取 provider 配置（服务解析时已填默认 60s / 2 次）。
    - credential_refs：显式 `credential_refs` 数组优先；缺省时用 api_key_env
      作为单条引用；api_key_env 也为空（如 local）→ keyless（空元组）。credential
      reference 即环境变量名，不带前缀（2026-07-17 复核，见 ADR-0011 §7）。
    - wait_threshold_seconds：应用级 [llm.retry]（ADR-0012 §2），所有 provider 共用。

明文 key 永不出现在这里——本模块只搬运环境变量名。
"""

from __future__ import annotations

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.gateway.provider_settings import (
    ProviderRuntimeSettings,
    ProviderSettingsSource,
)

_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_RETRIES = 2


class LlmConfigProviderSettingsSource(ProviderSettingsSource):
    """从 LlmConfigService + 封闭注册表现读 provider 运行时设置。"""

    def __init__(self, llm_config_service: LlmConfigService) -> None:
        self._llm = llm_config_service

    def settings_for(self, provider_id: str) -> ProviderRuntimeSettings:
        spec = provider_registry.require_known_provider(provider_id)
        provider = self._llm.config().provider(provider_id)
        wait_threshold = self._llm.retry_settings().wait_threshold_seconds
        refs: tuple[str, ...]
        if provider is None:
            # 未配置 provider 段：用注册表默认（api_key_env 即环境变量名引用）。
            refs = (spec.api_key_env,) if spec.api_key_env else ()
            return ProviderRuntimeSettings(
                provider_id=provider_id,
                timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                max_retries=_DEFAULT_MAX_RETRIES,
                credential_refs=refs,
                wait_threshold_seconds=wait_threshold,
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
            wait_threshold_seconds=wait_threshold,
        )
