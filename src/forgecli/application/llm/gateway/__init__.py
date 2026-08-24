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
    structured_schema_digest,
)
from forgecli.application.llm.gateway.catalog import ModelCatalogService
from forgecli.application.llm.gateway.credentials import (
    CredentialPool,
    CredentialResolver,
)
from forgecli.application.llm.gateway.default_gateway import DefaultLlmGateway
from forgecli.application.llm.gateway.errors import (
    MalformedToolCallError,
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
    SlidingWindowHealthRegistry,
)
from forgecli.application.llm.gateway.in_memory_catalog import InMemoryModelCatalog
from forgecli.application.llm.gateway.observability import (
    GatewayCallSample,
    InProcessGatewayMetrics,
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
from forgecli.application.llm.gateway.selection_resolver import (
    ModelSelectionResolver,
)
from forgecli.application.llm.gateway.streaming import StreamAccumulator
from forgecli.application.llm.gateway.token_estimator import (
    ApproximateTokenEstimator,
)
from forgecli.domain.conversation.message import (
    ChatMessage,
    ContentBlock,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.credentials import Credential
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import (
    ModelParams,
    ThinkingConfig,
)
from forgecli.domain.model.request import (
    ModelRequest,
    StructuredModelRequest,
)
from forgecli.domain.model.resolved import ResolvedModel
from forgecli.domain.model.response import (
    FinishReason,
    ModelResponse,
    ModelUsage,
    StructuredModelResponse,
)
from forgecli.domain.model.selection import (
    CurrentModelSelection,
    ExplicitModelSelection,
    ModelSelection,
    SelectionKind,
)
from forgecli.domain.model.streaming import (
    ModelStreamChunk,
    ProviderStreamChunk,
    ToolCallDelta,
)
from forgecli.domain.model.thinking import ThinkingEffortName, ThinkingMode
from forgecli.domain.tool.tool_call import ToolCall, ToolSchema
from forgecli.shared.cancellation import CancelToken
from forgecli.shared.json_schema import validate_json_schema

__all__ = [
    # 端口
    "LlmGateway",
    "ModelProvider",
    "ProviderCapabilities",
    "ProviderRequest",
    "ProviderResponse",
    # 实现 / 注册表（MVP）
    "DefaultLlmGateway",
    "ModelSelectionResolver",
    "ProviderRegistry",
    # 请求 DTO
    "ModelRequest",
    "StructuredModelRequest",
    "CancelToken",
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
    "ResolvedModel",
    # 凭证（§7）
    "Credential",
    "CredentialPool",
    "CredentialResolver",
    # provider 运行时设置
    "ProviderRuntimeSettings",
    "ProviderSettingsSource",
    # token 估算（§11.4 / ADR-0012 §6）
    "ApproximateTokenEstimator",
    # 超参
    "ModelParams",
    "ThinkingConfig",
    "ThinkingEffortName",
    "ThinkingMode",
    # message / tool 词汇
    "ChatMessage",
    "ContentBlock",
    "TextBlock",
    "ToolResultBlock",
    "ToolSchema",
    "ToolCall",
    # 流式（§9）
    "ModelStreamChunk",
    "ProviderStreamChunk",
    "StreamAccumulator",
    "ToolCallDelta",
    # 返回 DTO
    "ModelResponse",
    "ModelUsage",
    "StructuredModelResponse",
    "FinishReason",
    # 缓存（§14 / ADR-0012 §3）
    "InMemoryResponseCache",
    "structured_schema_digest",
    # 治理（§11.3 / §12.1，真实实现见 ADR-0012 §8）
    "SlidingWindowHealthRegistry",
    # 可观测性（ADR-0012 §9）
    "GatewayCallSample",
    "InProcessGatewayMetrics",
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
    "MalformedToolCallError",
    "ModelResponseParseError",
    "ModelBudgetExceededError",
    "ModelCancelledError",
]
