"""LLM 调用网关切片（ADR-0011）：端口 + 统一 DTO + 治理件 + 错误类型。

本切片是 application/llm 下与配置切片（llm/config）并列的调用切片。complete /
stream / complete_structured 三类调用共用同一控制面；凭证、计量、缓存、熔断、
预算等治理能力都从这里的端口注入（MVP 部分为 no-op，见 governance）。

故意*不*在 application/llm 顶层 __init__ re-export 这里的 ModelParams，避免与配置切片
同名的 ModelParams 冲突；通过 forgecli.application.llm.gateway 这一独立命名空间引用。
"""

from __future__ import annotations

from forgecli.application.llm.gateway.cache import (
    InMemoryResponseCache,
    LlmCacheController,
    NoopLlmCacheController,
    # structured_schema_digest,
)
from forgecli.application.llm.gateway.catalog import (
    ModelCatalogEntry,
    ModelCatalogService,
)
from forgecli.application.llm.gateway.credentials import (
    Credential,
    CredentialPool,
    CredentialResolver,
)
from forgecli.application.llm.gateway.default_gateway import DefaultLlmGateway
from forgecli.application.llm.gateway.default_selection_resolver import (
    DefaultModelSelectionResolver,
)
from forgecli.application.llm.gateway.errors import (
    ModelAuthError,
    ModelBadRequestError,
    ModelBudgetExceededError,
    ModelCancelledError,
    ModelContextOverflowError,
    ModelGatewayError,
    ModelProviderInternalError,
    ModelRateLimitError,
    ModelResponseParseError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.governance import (
    BudgetGuard,
    NoopBudgetGuard,
    NoopProviderHealthRegistry,
    ProviderHealthRegistry,
    SlidingWindowHealthRegistry,
    SnapshotBudgetGuard,
)
from forgecli.application.llm.gateway.in_memory_catalog import InMemoryModelCatalog
from forgecli.application.llm.gateway.messages import (
    ChatMessage,
    ContentBlock,
    TextBlock,
    ToolCall,
    ToolResultBlock,
    ToolSpec,
)
from forgecli.application.llm.gateway.observability import (
    GatewayCallSample,
    GatewayObserver,
    InProcessGatewayMetrics,
    NoopGatewayObserver,
)
from forgecli.application.llm.gateway.origin import RequestOrigin
from forgecli.application.llm.gateway.params import (
    ModelParams,
    ThinkingConfig,
    ThinkingEffort,
    ThinkingMode,
)
from forgecli.application.llm.gateway.provider import (
    ModelProvider,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResponse,
)
from forgecli.application.llm.gateway.provider_registry import ProviderRegistry
from forgecli.application.llm.gateway.provider_settings import (
    ProviderRuntimeSettings,
    ProviderSettingsSource,
)
from forgecli.application.llm.gateway.request import (
    BudgetSnapshot,
    CacheHint,
    CancelToken,
    ModelRequest,
    StructuredModelRequest,
)
from forgecli.application.llm.gateway.response import (
    FinishReason,
    ModelResponse,
    ModelUsage,
    StructuredModelResponse,
)
from forgecli.application.llm.gateway.schema_validation import validate_json_schema
from forgecli.application.llm.gateway.selection import (
    CurrentModelSelection,
    ExplicitModelSelection,
    ModelSelection,
    SelectionKind,
)
from forgecli.application.llm.gateway.selection_resolver import (
    ModelSelectionResolver,
    ResolvedModel,
)
# from forgecli.application.llm.gateway.streaming import (
#     ModelStreamChunk,
#     ProviderStreamChunk,
#     StreamAccumulator,
#     ToolCallDelta,
# )
from forgecli.application.llm.gateway.token_estimator import (
    ApproximateTokenEstimator,
    TokenEstimator,
)
from forgecli.application.llm.gateway.tokenizer_registry import TokenizerRegistry

__all__ = [
    # 端口
    "LlmGateway",
    "ModelProvider",
    "ProviderCapabilities",
    "ProviderRequest",
    "ProviderResponse",
    # 实现 / 注册表（MVP）
    "DefaultLlmGateway",
    "ProviderRegistry",
    # 请求 DTO
    "ModelRequest",
    "StructuredModelRequest",
    "BudgetSnapshot",
    "CancelToken",
    "CacheHint",
    # 用途 / 选择
    "RequestOrigin",
    "ModelSelection",
    "SelectionKind",
    "CurrentModelSelection",
    "ExplicitModelSelection",
    # 模型目录 / 选择解析（0701 契约冻结，0702 落地实现）
    "ModelCatalogEntry",
    "ModelCatalogService",
    "InMemoryModelCatalog",
    "ModelSelectionResolver",
    "DefaultModelSelectionResolver",
    "ResolvedModel",
    # 凭证（§7）
    "Credential",
    "CredentialPool",
    "CredentialResolver",
    # provider 运行时设置
    "ProviderRuntimeSettings",
    "ProviderSettingsSource",
    # token 估算（§11.4 / ADR-0012 §6）
    "TokenEstimator",
    "ApproximateTokenEstimator",
    "TokenizerRegistry",
    # 超参
    "ModelParams",
    "ThinkingConfig",
    "ThinkingMode",
    "ThinkingEffort",
    # message / tool 词汇
    "ChatMessage",
    "ContentBlock",
    "TextBlock",
    "ToolResultBlock",
    "ToolSpec",
    "ToolCall",
    # 流式（§9）
    # "ModelStreamChunk",
    # "ProviderStreamChunk",
    # "StreamAccumulator",
    # "ToolCallDelta",
    # 返回 DTO
    "ModelResponse",
    "ModelUsage",
    "StructuredModelResponse",
    "FinishReason",
    # 缓存（§14 / ADR-0012 §3）
    "LlmCacheController",
    "NoopLlmCacheController",
    "InMemoryResponseCache",
    "structured_schema_digest",
    # 治理（§11.3 / §12.1，真实实现见 ADR-0012 §8）
    "ProviderHealthRegistry",
    "NoopProviderHealthRegistry",
    "SlidingWindowHealthRegistry",
    "BudgetGuard",
    "NoopBudgetGuard",
    "SnapshotBudgetGuard",
    # 可观测性（ADR-0012 §9）
    "GatewayObserver",
    "GatewayCallSample",
    "InProcessGatewayMetrics",
    "NoopGatewayObserver",
    # 结构化输出校验
    "validate_json_schema",
    # 错误类型
    "ModelGatewayError",
    "ModelUnavailableError",
    "ModelAuthError",
    "ModelRateLimitError",
    "ModelTimeoutError",
    "ModelContextOverflowError",
    "ModelBadRequestError",
    "ModelProviderInternalError",
    "ModelResponseParseError",
    "ModelBudgetExceededError",
    "ModelCancelledError",
]
