"""模型的领域词汇: 引用, 选择, 超参, thinking, 调用来源, 请求与响应,
目录条目, 凭证, 流式增量, 用量计费, 供应商描述。"""

from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.credentials import Credential
from forgecli.domain.model.model_ref import InvalidModelRef, ModelRef
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams, ThinkingConfig
from forgecli.domain.model.provider_spec import ProviderSpec, ThinkingDialect
from forgecli.domain.model.request import (
    BudgetSnapshot,
    CacheHint,
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
from forgecli.domain.model.thinking import (
    ModelThinkingCapabilities,
    ModelThinkingSettings,
    ThinkingEffortName,
    ThinkingMode,
)
from forgecli.domain.model.thinking_override import ThinkingOverride
from forgecli.domain.model.usage import UnitPrices, UsageRecordDraft

__all__ = [
    "BudgetSnapshot",
    "CacheHint",
    "Credential",
    "CurrentModelSelection",
    "ExplicitModelSelection",
    "FinishReason",
    "InvalidModelRef",
    "ModelCatalogEntry",
    "ModelParams",
    "ModelRef",
    "ModelRequest",
    "ModelResponse",
    "ModelSelection",
    "ModelStreamChunk",
    "ModelThinkingCapabilities",
    "ModelThinkingSettings",
    "ModelUsage",
    "ProviderSpec",
    "ProviderStreamChunk",
    "RequestOrigin",
    "ResolvedModel",
    "SelectionKind",
    "StructuredModelRequest",
    "StructuredModelResponse",
    "ThinkingConfig",
    "ThinkingDialect",
    "ThinkingEffortName",
    "ThinkingMode",
    "ThinkingOverride",
    "ToolCallDelta",
    "UnitPrices",
    "UsageRecordDraft",
]
