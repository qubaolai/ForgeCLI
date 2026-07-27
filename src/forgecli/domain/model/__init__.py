"""模型的领域词汇: 引用, 选择, 超参, thinking, 调用来源, 响应。"""

from forgecli.domain.model.model_ref import InvalidModelRef, ModelRef
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams, ThinkingConfig
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
from forgecli.domain.model.thinking import (
    ModelThinkingCapabilities,
    ModelThinkingSettings,
    ThinkingEffortName,
    ThinkingMode,
)

__all__ = [
    "CurrentModelSelection",
    "ExplicitModelSelection",
    "FinishReason",
    "InvalidModelRef",
    "ModelParams",
    "ModelRef",
    "ModelResponse",
    "ModelSelection",
    "ModelThinkingCapabilities",
    "ModelThinkingSettings",
    "ModelUsage",
    "RequestOrigin",
    "SelectionKind",
    "StructuredModelResponse",
    "ThinkingConfig",
    "ThinkingEffortName",
    "ThinkingMode",
]
