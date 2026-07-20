"""模型目录运行时视图构建（ADR-0011 §4）。

目录来源：以代码内置目录为基线，`[llm.providers.*.models.*]` 中用户配置的模型
元数据按 provider+model 合并覆盖到基线之上，得到运行时只读视图（InMemoryModelCatalog）。
gateway、selection resolver、CostEstimator 只读该视图，不各自解析 TOML。

配置字段 -> 目录字段的映射：
    - context_window / max_tokens -> context_window / max_output_tokens。
      未配置且基线也没有时，用保守默认 ``_DEFAULT_CONTEXT_WINDOW``（32768）。
    - cost_per_1k_input / cost_per_1k_output / cost_per_1k_cached_input /
      cost_per_1k_reasoning（> 0 时）-> 价格；0 视为未声明（沿用基线价格或
      None，None 表示价格未知，不阻塞调用）。cached / reasoning 单价为
      ADR-0012 §7 新增，CostEstimator 消费。
    - extra 里的约定能力位（bool）：supports_tools -> supports_tool_calling、
      supports_json_schema -> supports_structured_output、supports_thinking、
      allowlisted、deprecated。非 bool 值忽略（extra 是自由透传段）。
"""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.application.llm.config.llm_config import LlmConfig, ModelSpec
from forgecli.application.llm.gateway.catalog import ModelCatalogEntry
from forgecli.application.llm.gateway.in_memory_catalog import InMemoryModelCatalog
from forgecli.application.llm.gateway.params import ThinkingEffort, ThinkingMode

# 用户未声明 context_window 且基线缺失时的保守默认（避免目录条目无法构造）。
_DEFAULT_CONTEXT_WINDOW = 32768

# 内置基线：只收录能力位随协议稳定的已知模型；价格随行情变动，不入代码，
# 由用户配置（cost_per_1k_*）提供。
BUILTIN_ENTRIES: tuple[ModelCatalogEntry, ...] = (
    ModelCatalogEntry(
        provider="deepseek",
        model="deepseek-chat",
        context_window=65536,
        max_output_tokens=8192,
        supports_structured_output=True,
        supports_tool_calling=True,
    ),
    ModelCatalogEntry(
        provider="deepseek",
        model="deepseek-reasoner",
        context_window=65536,
        max_output_tokens=8192,
        supports_thinking=True,
    ),
)


def build_catalog(config: LlmConfig) -> InMemoryModelCatalog:
    """内置基线 + 用户配置合并成运行时只读目录视图。"""
    merged: dict[tuple[str, str], ModelCatalogEntry] = {
        (entry.provider, entry.model): entry for entry in BUILTIN_ENTRIES
    }
    for spec in config.all_models():
        key = (spec.provider, spec.id)
        merged[key] = _merge_entry(merged.get(key), spec)
    return InMemoryModelCatalog(merged.values())


def _merge_entry(
    baseline: ModelCatalogEntry | None, spec: ModelSpec
) -> ModelCatalogEntry:
    params = spec.params
    flags = _capability_flags(params.extra)

    def flag(name: str, base_default: bool) -> bool:
        configured = flags.get(name)
        if configured is not None:
            return configured
        if baseline is not None:
            return bool(getattr(baseline, name))
        return base_default

    context_window = params.context_window
    if context_window is None:
        context_window = (
            baseline.context_window if baseline else _DEFAULT_CONTEXT_WINDOW
        )
    max_output = params.max_tokens
    if max_output is None and baseline is not None:
        max_output = baseline.max_output_tokens

    def price(configured: float, base_price: float | None) -> float | None:
        if configured > 0:
            return configured
        return base_price

    return ModelCatalogEntry(
        provider=spec.provider,
        model=spec.id,
        context_window=context_window,
        max_output_tokens=max_output,
        supports_structured_output=flag("supports_structured_output", False),
        supports_tool_calling=flag("supports_tool_calling", False),
        supports_thinking=flag("supports_thinking", False),
        thinking_mode=params.thinking_mode
        or (baseline.thinking_mode if baseline else ThinkingMode.AUTO),
        thinking_effort=params.thinking_effort
        or (baseline.thinking_effort if baseline else ThinkingEffort.NONE),
        allowlisted=flag("allowlisted", True),
        deprecated=flag("deprecated", False),
        input_price_per_1k=price(
            params.cost_per_1k_input,
            baseline.input_price_per_1k if baseline else None,
        ),
        output_price_per_1k=price(
            params.cost_per_1k_output,
            baseline.output_price_per_1k if baseline else None,
        ),
        cached_input_price_per_1k=price(
            params.cost_per_1k_cached_input,
            baseline.cached_input_price_per_1k if baseline else None,
        ),
        reasoning_price_per_1k=price(
            params.cost_per_1k_reasoning,
            baseline.reasoning_price_per_1k if baseline else None,
        ),
    )


# extra 约定键 -> ModelCatalogEntry 字段名。
_EXTRA_FLAG_KEYS: dict[str, str] = {
    "supports_tools": "supports_tool_calling",
    "supports_json_schema": "supports_structured_output",
    "supports_thinking": "supports_thinking",
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
