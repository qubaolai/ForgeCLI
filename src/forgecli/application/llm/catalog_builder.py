"""模型目录运行时视图构建（ADR-0011 §4）。

目录唯一来源是 `[llm.providers.*.models.*]` 中的用户配置。代码只内置供应商和
adapter，不内置任何具体模型或模型能力。gateway、selection resolver、CostEstimator
只读构建后的 InMemoryModelCatalog，不各自解析 TOML。

配置字段 -> 目录字段的映射：
    - context_window / max_tokens -> context_window / max_output_tokens。
      context_window 未配置时使用保守默认 ``_DEFAULT_CONTEXT_WINDOW``（32768）。
    - cost_per_1k_input / cost_per_1k_output / cost_per_1k_cached_input /
      cost_per_1k_reasoning（> 0 时）-> 价格；0 视为未声明，目录中使用 None，
      表示价格未知且不阻塞调用。cached / reasoning 单价为
      ADR-0012 §7 新增，CostEstimator 消费。
    - extra 里的约定能力位（bool）：supports_tools -> supports_tool_calling、
      supports_json_schema -> supports_structured_output、allowlisted、deprecated。
      非 bool 值忽略（extra 是自由透传段）。
"""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.application.llm.catalog import InMemoryModelCatalog
from forgecli.application.llm.config.llm_config import LlmConfig, ModelSpec
from forgecli.application.llm.errors import ConfigValidationError
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.thinking import (
    ModelThinkingCapabilities,
    ModelThinkingSettings,
    ThinkingMode,
)

# 用户未声明 context_window 时的保守默认（避免目录条目无法构造）。
_DEFAULT_CONTEXT_WINDOW = 32768


def build_catalog(config: LlmConfig) -> InMemoryModelCatalog:
    """把用户配置的模型构建成运行时只读目录视图。"""
    return InMemoryModelCatalog(_build_entry(spec) for spec in config.all_models())


def _thinking(
    spec: ModelSpec,
) -> tuple[ModelThinkingCapabilities, ModelThinkingSettings]:
    params = spec.params
    try:
        capabilities = ModelThinkingCapabilities(
            efforts=params.thinking_efforts or (),
            default_effort=params.thinking_default_effort,
        )
        settings = ModelThinkingSettings(
            mode=params.thinking_mode or ThinkingMode.OFF,
            effort=params.thinking_effort,
        )
        capabilities.validate(settings)
    except ValueError as exc:
        raise ConfigValidationError(
            f"模型 {spec.provider}:{spec.id} 的 thinking 配置无效：{exc}"
        ) from exc

    return capabilities, settings


def _build_entry(spec: ModelSpec) -> ModelCatalogEntry:
    params = spec.params
    flags = _capability_flags(params.extra)

    thinking_capabilities, thinking_settings = _thinking(spec)

    def flag(name: str, default: bool) -> bool:
        configured = flags.get(name)
        return configured if configured is not None else default

    def price(configured: float) -> float | None:
        return configured if configured > 0 else None

    return ModelCatalogEntry(
        provider=spec.provider,
        model=spec.id,
        context_window=params.context_window or _DEFAULT_CONTEXT_WINDOW,
        max_output_tokens=params.max_tokens,
        supports_structured_output=flag("supports_structured_output", False),
        supports_tool_calling=flag("supports_tool_calling", False),
        thinking_capabilities=thinking_capabilities,
        thinking_settings=thinking_settings,
        allowlisted=flag("allowlisted", True),
        deprecated=flag("deprecated", False),
        input_price_per_1k=price(params.cost_per_1k_input),
        output_price_per_1k=price(params.cost_per_1k_output),
        cached_input_price_per_1k=price(params.cost_per_1k_cached_input),
        reasoning_price_per_1k=price(params.cost_per_1k_reasoning),
    )


# extra 约定键 -> ModelCatalogEntry 字段名。
_EXTRA_FLAG_KEYS: dict[str, str] = {
    "supports_tools": "supports_tool_calling",
    "supports_json_schema": "supports_structured_output",
    "allowlisted": "allowlisted",
    "deprecated": "deprecated",
}


def _capability_flags(extra: Mapping[str, object]) -> dict[str, bool]:
    """从 extra 提取约定能力位；只认 bool 值，其余忽略（extra 是自由段）。"""
    flags: dict[str, bool] = {}
    for key, field_name in _EXTRA_FLAG_KEYS.items():
        value = extra.get(key)
        if isinstance(value, bool):
            flags[field_name] = value
    return flags
