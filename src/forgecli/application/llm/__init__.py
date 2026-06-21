"""LLM 领域：封闭供应商 + 配置驱动的模型，分「配置」与「调用」两个切片。

- 共享词汇（顶层）：providers（封闭注册表）、catalog（值对象）、errors。
- 配置切片：llm/config（读写 .forge/llm.toml）。
- 调用切片：llm/client（端口占位，adapter 待 infrastructure/llm/adapters 实现）。
"""

from __future__ import annotations

from forgecli.application.llm.catalog import (
    LlmConfig,
    ModelParams,
    ModelSpec,
    ProviderConfig,
)
from forgecli.application.llm.config import (
    FileLlmConfigService,
    LlmConfigService,
    LlmConfigStore,
)
from forgecli.application.llm.errors import (
    ConfigReadError,
    ConfigValidationError,
    UnknownProvider,
)
from forgecli.application.llm.providers import (
    REGISTRY,
    ProviderSpec,
    is_known_provider,
    require_known_provider,
)

__all__ = [
    "LlmConfig",
    "ProviderConfig",
    "ModelSpec",
    "ModelParams",
    "LlmConfigService",
    "FileLlmConfigService",
    "LlmConfigStore",
    "ProviderSpec",
    "REGISTRY",
    "is_known_provider",
    "require_known_provider",
    "UnknownProvider",
    "ConfigReadError",
    "ConfigValidationError",
]
