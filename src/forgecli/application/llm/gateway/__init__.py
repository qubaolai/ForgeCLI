"""LLM 调用网关（ADR-0011）：端口 + 统一 DTO + 错误类型。

本切片是 application/llm 下与配置切片（llm/config）并列的调用切片，只冻结边界：
不接真实 provider SDK，不实现路由 / fallback / streaming / tool calling / 结构化校验。

故意不在 application/llm 顶层 __init__ re-export 这里的 ModelParams，避免与配置切片
同名的 ModelParams 冲突；通过 forgecli.application.llm.gateway 这一独立命名空间引用。
"""

from __future__ import annotations

from forgecli.application.llm.gateway.default_gateway import DefaultLlmGateway
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
from forgecli.application.llm.gateway.messages import (
    ChatMessage,
    ContentBlock,
    TextBlock,
    ToolCall,
    ToolSpec,
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
from forgecli.application.llm.gateway.request import (
    BudgetSnapshot,
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
from forgecli.application.llm.gateway.selection import (
    CurrentModelSelection,
    ExplicitModelSelection,
    ModelSelection,
    SelectionKind,
    TierModelSelection,
)

__all__ = [
    # 端口
    "LlmGateway",
    "ModelProvider",
    "ProviderCapabilities",
    "ProviderRequest",
    "ProviderResponse",
    # 网关实现 / 注册表
    "DefaultLlmGateway",
    "ProviderRegistry",
    # 请求 DTO
    "ModelRequest",
    "StructuredModelRequest",
    "BudgetSnapshot",
    "CancelToken",
    # 用途 / 选择
    "RequestOrigin",
    "ModelSelection",
    "SelectionKind",
    "CurrentModelSelection",
    "TierModelSelection",
    "ExplicitModelSelection",
    # 超参
    "ModelParams",
    "ThinkingConfig",
    "ThinkingMode",
    "ThinkingEffort",
    # message / tool 词汇
    "ChatMessage",
    "ContentBlock",
    "TextBlock",
    "ToolSpec",
    "ToolCall",
    # 返回 DTO
    "ModelResponse",
    "ModelUsage",
    "StructuredModelResponse",
    "FinishReason",
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
