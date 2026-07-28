"""LLM 领域：封闭供应商 + 配置驱动的模型，分「配置」与「调用」两个切片。

- 共享词汇（顶层）：providers（封闭注册表）、model_ref（默认模型引用）、errors。
- 配置切片：llm/config（读写 .forge/llm.toml）。
- 调用切片：待接入（adapter 将在 infrastructure/llm/adapters 实现）。
"""

from __future__ import annotations

from forgecli.application.llm.config import (
    LlmConfigService,
    LlmConfigStore,
)
from forgecli.application.llm.config.llm_config import (
    LlmConfig,
    ModelParams,
    ModelSpec,
    ProviderConfig,
)
from forgecli.application.llm.errors import (
    ConfigReadError,
    ConfigValidationError,
    UnknownProvider,
)
from forgecli.application.llm.providers import (
    REGISTRY,
    is_known_provider,
    require_known_provider,
)
from forgecli.domain.model.provider_spec import ProviderSpec

__all__ = [
    "LlmConfig",
    "ProviderConfig",
    "ModelSpec",
    "ModelParams",
    "LlmConfigService",
    "LlmConfigStore",
    "ProviderSpec",
    "REGISTRY",
    "is_known_provider",
    "require_known_provider",
    "UnknownProvider",
    "ConfigReadError",
    "ConfigValidationError",
]
