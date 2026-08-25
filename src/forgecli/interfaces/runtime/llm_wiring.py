"""界面无关的 LLM 网关运行时装配。

CLI 与 Web 必须共用这一组合根。只有这里知道网关的全部具体实现：
OpenAI-compatible adapter 的注册（含 thinking
方言）、凭证解析器（env 环境变量）/ 凭证池、动态选择解析器、token 估算与分词器
注册表、计量、治理件（熔断 / 预算 / 响应缓存，按 llm.json 配置段驱动，未启用
未启用即空转）与进程内观测聚合。application / AgentTurn 只依赖 LlmGateway 端口，
不 import httpx 或任何 adapter（§19）；chat 主路径由 bootstrap 组装
BuiltinAgentLoop 驱动（ADR-0010），本模块交回网关与计量件。

不装配客户端主动限流：单用户单 key 的个人 CLI 场景下本地限流没有信息优势，
429 由 gateway 自身的短等重试环处理（详见 gateway/governance.py 模块说明）。

注册规则（§6 封闭注册表）：只为 api_base 非空的 provider 绑定
OpenAICompatibleProvider（deepseek 有代码级默认 base；其余需在 /config 供应商
配置里填写 api_base）。api_base 在启动后修改需重启进程生效——adapter 的
base_url 在注册时固定（阶段偏差，路由与模型选择本身是现读的）。治理件与
缓存同样在装配期按当时配置构建，修改后重启生效。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from forgecli.application.config.config_service import ConfigService
from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.catalog import ModelCatalogService
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.gateway.cache import InMemoryResponseCache
from forgecli.application.llm.gateway.default_gateway import DefaultLlmGateway
from forgecli.application.llm.gateway.errors import ModelGatewayError
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.governance import SlidingWindowHealthRegistry
from forgecli.application.llm.gateway.observability import InProcessGatewayMetrics
from forgecli.application.llm.gateway.provider_registry import ProviderRegistry
from forgecli.application.llm.metering import CostEstimator, UsageMeter
from forgecli.application.llm.overrides_service import ModelOverridesService
from forgecli.application.llm.selection import ConfigBackedSelectionResolver
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
from forgecli.domain.config.errors import ConfigError
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.selection import CurrentModelSelection
from forgecli.infrastructure.llm.adapters import OpenAICompatibleProvider
from forgecli.infrastructure.llm.credentials import (
    EnvCredentialResolver,
    InMemoryCredentialPool,
)
from forgecli.infrastructure.llm.overrides_json_store import JsonModelOverridesStore
from forgecli.infrastructure.llm.settings import LlmConfigProviderSettingsSource


@dataclass(frozen=True)
class LlmRuntime:
    """装配完成的 LLM 运行时：网关 + usage 计量件 + 覆盖配置服务 + 观测聚合。"""

    gateway: LlmGateway
    usage_meter: UsageMeter
    overrides_service: ModelOverridesService
    # 进程内观测聚合（ADR-0012 §9）：/status 经 snapshot() 消费（展示接入后续切片）。
    gateway_metrics: InProcessGatewayMetrics
    thinking_state: ThinkingRuntimeState
    # 当前模型的上下文预算 (ADR-0032 决策 1). 每轮现取: 用户可能刚 /model 换过模型.
    context_budget: Callable[[], ContextBudget | None]


def build_llm_runtime(
    config_service: ConfigService,
    llm_config_service: LlmConfigService,
    forge_json: Path,
    thinking_state: ThinkingRuntimeState | None = None,
) -> LlmRuntime:
    """装配统一 LLM 网关及其协作件"""
    if thinking_state is None:
        thinking_state = ThinkingRuntimeState()
    overrides_service = ModelOverridesService(
        JsonModelOverridesStore(forge_json), llm_config_service
    )
    resolver = ConfigBackedSelectionResolver(
        config_service=config_service,
        llm_config_service=llm_config_service,
        overrides_loader=overrides_service.overrides,
        thinking_state=thinking_state,
    )
    registry = _build_provider_registry(llm_config_service)

    metrics = InProcessGatewayMetrics()
    gateway = DefaultLlmGateway(
        registry,
        resolver=resolver,
        settings_source=LlmConfigProviderSettingsSource(llm_config_service),
        # 凭证只支持环境变量（ADR-0011 §7 复核）：dotenv / keychain 已移除。
        credential_pool=InMemoryCredentialPool(EnvCredentialResolver()),
        health_registry=_build_health_registry(llm_config_service),
        cache=_build_response_cache(llm_config_service),
        observer=metrics,
    )

    # CostEstimator 现读目录视图：包一层动态 catalog，价格随 /config 修改生效。
    usage_meter = UsageMeter(CostEstimator(_DynamicCatalog(llm_config_service)))
    return LlmRuntime(
        gateway=gateway,
        usage_meter=usage_meter,
        overrides_service=overrides_service,
        gateway_metrics=metrics,
        thinking_state=thinking_state,
        context_budget=_budget_reader(resolver),
    )


def _budget_reader(
    resolver: ConfigBackedSelectionResolver,
) -> Callable[[], ContextBudget | None]:
    """把当前主模型的窗口折成一份预算 (ADR-0032 决策 1).

    走 resolver 而不是直接读 llm.json: 当前模型是谁, 有没有按用途覆盖, 目录里的窗口
    是多少 —— 这三件事的口径必须与真正发请求时用的那一份完全一致. 各读各的, 压缩就会
    按 A 模型的窗口去压一份要发给 B 模型的请求.

    解析不出来时返回 None, **不猜一个窗口**: 猜小了平白压掉内容, 猜大了等于没有这道
    防线. 返回 None 只是回到接入压缩之前的行为, 而那不会让这一轮失败.
    """

    def _current() -> ContextBudget | None:
        try:
            resolved = resolver.resolve(
                CurrentModelSelection(), origin=RequestOrigin.ACT
            )
        except (ModelGatewayError, ConfigError):
            # 模型没配, 目录里没有它, 或者配置读不了. 都不是这一轮该失败的理由.
            return None
        entry = resolved.entry
        return ContextBudget(
            context_window=entry.context_window,
            reserved_output_tokens=entry.max_output_tokens or 0,
        )

    return _current


def _build_provider_registry(llm_config_service: LlmConfigService) -> ProviderRegistry:
    registry = ProviderRegistry()
    config = llm_config_service.config()
    for provider_id, spec in provider_registry.REGISTRY.items():
        provider_config = config.provider(provider_id)
        api_base = (
            provider_config.api_base if provider_config else spec.default_api_base
        )
        if not api_base.strip():
            continue  # 无 api_base 的 provider 不绑定 adapter -> ModelUnavailableError
        registry.register(
            OpenAICompatibleProvider(
                provider_id=provider_id,
                base_url=api_base,
                thinking_dialect=spec.thinking_dialect,
            )
        )
    return registry


def _build_response_cache(llm: LlmConfigService) -> InMemoryResponseCache:
    """[llm.cache] 驱动（ADR-0012 §3）：未启用 = 空白名单，lookup/store 都不做事。"""
    settings = llm.cache_settings()
    return InMemoryResponseCache(
        allowed_origins=settings.origins if settings.enabled else (),
        ttl_seconds=settings.ttl_seconds,
        max_entries=settings.max_entries,
    )


def _build_health_registry(llm: LlmConfigService) -> SlidingWindowHealthRegistry:
    """[llm.circuit_breaker] 驱动（ADR-0012 §8）：未启用时三个方法都直通。"""
    settings = llm.circuit_breaker_settings()
    return SlidingWindowHealthRegistry(
        enabled=settings.enabled,
        failure_threshold=settings.failure_threshold,
        cooldown_seconds=settings.cooldown_seconds,
    )


class _DynamicCatalog(ModelCatalogService):
    """目录的动态视图：每次读都从 llm 配置重建（价格改动随 /config 生效）。"""

    def __init__(self, llm_config_service: LlmConfigService) -> None:
        self._llm = llm_config_service

    def has_model(self, ref: ModelRef) -> bool:
        return build_catalog(self._llm.config()).has_model(ref)

    def get(self, ref: ModelRef) -> ModelCatalogEntry:
        return build_catalog(self._llm.config()).get(ref)
