"""LLM 配置的不可变值对象与模型参数解析 / 校验（配置切片与调用切片共享词汇）。

层次：
    LlmConfig             整份有效配置（多家供应商）
      └ ProviderConfig    一家供应商（元数据 + 其模型）
          └ ModelSpec     一个模型（标识 + 参数）
              └ ModelParams  模型参数：能力/计费元数据 + 标准超参 + 厂商自定义(extra)

参数分两类（对齐需求）：
    - 标准化字段：context_window / max_tokens / cost_* / temperature / top_p，
      带类型与范围校验。
    - 厂商自定义：extra，任意 JSON object，原样透传，只校验「是个对象」。

这些值对象将来由调用切片（adapter）直接消费来构造请求。它们也是 /model 面板选择
运行时默认模型时的唯一模型来源。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.application.llm.errors import ConfigValidationError
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.thinking import (
    ModelThinkingCapabilities,
    ModelThinkingSettings,
    ThinkingEffortName,
    ThinkingMode,
)

# 标准化字段名（出现在模型行内表里、且我们认识的键）。其余键归入 extra。
_KNOWN_FIELDS = {
    "context_window",
    "max_tokens",
    "cost_per_1k_input",
    "cost_per_1k_output",
    "cost_per_1k_cached_input",
    "cost_per_1k_reasoning",
    "temperature",
    "top_p",
    "thinking_mode",
    "thinking_effort",
    "thinking_efforts",
    "thinking_default_effort",
    "extra",
}


def _as_positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{name} 必须是整数，收到: {value!r}")
    if value <= 0:
        raise ConfigValidationError(f"{name} 必须为正整数，收到: {value!r}")
    return value


def _as_nonneg_float(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigValidationError(f"{name} 必须是数字，收到: {value!r}")
    number = float(value)
    if number < 0:
        raise ConfigValidationError(f"{name} 不能为负，收到: {value!r}")
    return number


def _as_ranged_float(name: str, value: object, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigValidationError(f"{name} 必须是数字，收到: {value!r}")
    number = float(value)
    if not lo <= number <= hi:
        raise ConfigValidationError(f"{name} 必须在 [{lo}, {hi}] 内，收到: {value!r}")
    return number


def _as_thinking_mode(value: object) -> ThinkingMode:
    if not isinstance(value, str):
        raise ConfigValidationError(f"thinking_mode 必须是字符串，收到: {value!r}")
    try:
        return ThinkingMode(value)
    except ValueError:
        allowed = " / ".join(item.value for item in ThinkingMode)
        raise ConfigValidationError(
            f"thinking_mode 只能是 [{allowed}]，收到: {value!r}"
        ) from None


def _as_effort_name(name: str, value: object) -> ThinkingEffortName:
    if not isinstance(value, str):
        raise ConfigValidationError(f"{name} must be a string")

    try:
        return ThinkingEffortName(value)
    except ValueError as exc:
        raise ConfigValidationError(f"invalid {name}: {value}") from exc


def _as_effort_names(value: object) -> tuple[ThinkingEffortName, ...]:
    if not isinstance(value, list):
        raise ConfigValidationError("thinking_efforts must be a list")

    return tuple(
        _as_effort_name(f"thinking_efforts[{index}]", item)
        for index, item in enumerate(value)
    )


@dataclass(frozen=True)
class StandardField:
    """一个标准化模型字段的 UI / 解析元数据。"""

    name: str
    label: str
    kind: type  # int | float | str
    choices: tuple[str, ...] = ()


# 可在交互式菜单里逐项编辑的标准字段（顺序即菜单顺序）。extra 走单独的 JSON 编辑。
# cached / reasoning 单价为 ADR-0012 §7 新增；/config 模型编辑菜单随本表自动获得两行。
STANDARD_FIELDS: tuple[StandardField, ...] = (
    StandardField("context_window", "上下文窗口", int),
    StandardField("max_tokens", "最大输出 tokens", int),
    StandardField("temperature", "温度", float),
    StandardField("top_p", "top_p", float),
    StandardField("cost_per_1k_input", "输入价格/1k", float),
    StandardField("cost_per_1k_output", "输出价格/1k", float),
    StandardField("cost_per_1k_cached_input", "缓存输入价格/1k", float),
    StandardField("cost_per_1k_reasoning", "思考输出价格/1k", float),
)

_FIELD_KIND: dict[str, type] = {f.name: f.kind for f in STANDARD_FIELDS}


def coerce_field(name: str, raw: str) -> int | float | str:
    """把菜单文本输入转成字段的原生类型；不能解析时抛 ConfigValidationError。

    只做类型转换，范围校验仍由 ModelParams.parse 统一负责。
    """
    kind = _FIELD_KIND.get(name)
    if kind is None:
        raise ConfigValidationError(f"未知模型字段: {name}")
    text = raw.strip()
    field = next(item for item in STANDARD_FIELDS if item.name == name)
    if kind is str:
        if text not in field.choices:
            allowed = " / ".join(field.choices)
            raise ConfigValidationError(f"{name} 只能是 [{allowed}]，收到: {raw!r}")
        return text
    try:
        return int(text) if kind is int else float(text)
    except ValueError:
        expected = "整数" if kind is int else "数字"
        raise ConfigValidationError(f"{name} 必须是{expected}，收到: {raw!r}") from None


def parse_extra(raw: str) -> dict[str, object]:
    """把一行 JSON 文本解析成 extra 对象；非法或非对象时抛 ConfigValidationError。"""
    text = raw.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"extra 不是合法 JSON：{exc}") from None
    if not isinstance(value, dict):
        raise ConfigValidationError("extra 必须是 JSON 对象（{...}）")
    return value


@dataclass(frozen=True)
class ModelParams:
    context_window: int | None = None
    max_tokens: int | None = None
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    cost_per_1k_cached_input: float = 0.0
    cost_per_1k_reasoning: float = 0.0
    temperature: float | None = None
    top_p: float | None = None

    # 模型 thinking 强度声明。
    thinking_efforts: tuple[ThinkingEffortName, ...] | None = None
    thinking_default_effort: ThinkingEffortName | None = None

    # 用户对当前模型的 thinking 设置。
    thinking_mode: ThinkingMode | None = None
    thinking_effort: ThinkingEffortName | None = None

    extra: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def to_fields(self) -> dict[str, object]:
        """转回可写入 TOML 的字段 dict，只包含已设置的项目。"""
        out: dict[str, object] = {}

        if self.context_window is not None:
            out["context_window"] = self.context_window
        if self.max_tokens is not None:
            out["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            out["temperature"] = self.temperature
        if self.top_p is not None:
            out["top_p"] = self.top_p

        if self.thinking_efforts is not None:
            out["thinking_efforts"] = [effort.value for effort in self.thinking_efforts]
        if self.thinking_default_effort is not None:
            out["thinking_default_effort"] = self.thinking_default_effort.value
        if self.thinking_mode is not None:
            out["thinking_mode"] = self.thinking_mode.value
        if self.thinking_effort is not None:
            out["thinking_effort"] = self.thinking_effort.value

        if self.cost_per_1k_input:
            out["cost_per_1k_input"] = self.cost_per_1k_input
        if self.cost_per_1k_output:
            out["cost_per_1k_output"] = self.cost_per_1k_output
        if self.cost_per_1k_cached_input:
            out["cost_per_1k_cached_input"] = self.cost_per_1k_cached_input
        if self.cost_per_1k_reasoning:
            out["cost_per_1k_reasoning"] = self.cost_per_1k_reasoning

        if self.extra:
            out["extra"] = dict(self.extra)

        return out

    def extra_json(self) -> str:
        """extra 的单行 JSON 文本，供菜单展示与编辑初值。"""
        if not self.extra:
            return ""
        return json.dumps(dict(self.extra), ensure_ascii=False)

    @classmethod
    def parse(cls, raw: Mapping[str, object]) -> ModelParams:
        """从模型配置构造参数对象；非法值抛 ConfigValidationError。"""
        context_window = raw.get("context_window")
        max_tokens = raw.get("max_tokens")
        temperature = raw.get("temperature")
        top_p = raw.get("top_p")
        extra = raw.get("extra", {})

        if not isinstance(extra, Mapping):
            raise ConfigValidationError("extra 必须是 JSON 对象（键值表）")

        # 未被 ForgeCLI 标准化的字段兼容性地归入 extra，
        # 供具体供应商 adapter 使用。
        merged_extra = {
            key: value for key, value in raw.items() if key not in _KNOWN_FIELDS
        }
        merged_extra.update(extra)

        thinking_efforts = (
            _as_effort_names(raw["thinking_efforts"])
            if "thinking_efforts" in raw
            else None
        )

        thinking_default_effort = (
            _as_effort_name(
                "thinking_default_effort",
                raw["thinking_default_effort"],
            )
            if "thinking_default_effort" in raw
            else None
        )

        thinking_mode = (
            _as_thinking_mode(raw["thinking_mode"]) if "thinking_mode" in raw else None
        )

        thinking_effort = (
            _as_effort_name(
                "thinking_effort",
                raw["thinking_effort"],
            )
            if "thinking_effort" in raw
            else None
        )

        try:
            capabilities = ModelThinkingCapabilities(
                efforts=thinking_efforts or (),
                default_effort=thinking_default_effort,
            )
            capabilities.validate(
                ModelThinkingSettings(
                    mode=thinking_mode or ThinkingMode.OFF,
                    effort=thinking_effort,
                )
            )
        except ValueError as exc:
            raise ConfigValidationError(str(exc)) from exc

        return cls(
            context_window=(
                None
                if context_window is None
                else _as_positive_int(
                    "context_window",
                    context_window,
                )
            ),
            max_tokens=(
                None
                if max_tokens is None
                else _as_positive_int(
                    "max_tokens",
                    max_tokens,
                )
            ),
            cost_per_1k_input=_as_nonneg_float(
                "cost_per_1k_input",
                raw.get("cost_per_1k_input", 0.0),
            ),
            cost_per_1k_output=_as_nonneg_float(
                "cost_per_1k_output",
                raw.get("cost_per_1k_output", 0.0),
            ),
            cost_per_1k_cached_input=_as_nonneg_float(
                "cost_per_1k_cached_input",
                raw.get("cost_per_1k_cached_input", 0.0),
            ),
            cost_per_1k_reasoning=_as_nonneg_float(
                "cost_per_1k_reasoning",
                raw.get("cost_per_1k_reasoning", 0.0),
            ),
            temperature=(
                None
                if temperature is None
                else _as_ranged_float(
                    "temperature",
                    temperature,
                    0.0,
                    2.0,
                )
            ),
            top_p=(
                None
                if top_p is None
                else _as_ranged_float(
                    "top_p",
                    top_p,
                    0.0,
                    1.0,
                )
            ),
            thinking_efforts=thinking_efforts,
            thinking_default_effort=thinking_default_effort,
            thinking_mode=thinking_mode,
            thinking_effort=thinking_effort,
            extra=MappingProxyType(dict(merged_extra)),
        )


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    id: str
    params: ModelParams


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    name: str
    api_base: str
    api_key_env: str | None
    timeout: int
    max_retries: int
    models: tuple[ModelSpec, ...]
    # 可选 key pool（ADR-0011 §7 / §16）：多个 credential reference（环境变量名，
    # 如 "DEEPSEEK_API_KEY"）。缺省时由 settings 层从 api_key_env 派生单条引用。
    # 只支持 TOML 手写，不进 /config 菜单编辑；明文 key 永不入配置。
    credential_refs: tuple[str, ...] = ()

    def model(self, model_id: str) -> ModelSpec | None:
        return next((m for m in self.models if m.id == model_id), None)


# ---- 网关运行时配置段（ADR-0012）----
# 以下值对象对应 llm.json  的 llm.cache / llm.circuit_breaker / llm.retry，
# 由 LlmConfigService 解析、wiring 消费装配。所有能力默认关闭或回落现行为；
# 网关自身不解析 TOML（ADR-0011 §4 边界不变）。已移除的配置段：[llm.rate_limit]
# （客户端主动限流，见 governance.py）、[llm.credentials]（凭证只支持环境变量，
# 无 dotenv 开关，见 infrastructure/llm/credentials.py）。


# 响应缓存 origins 白名单的默认值（ADR-0012 §3 配置示例）。
_DEFAULT_CACHE_ORIGINS = (
    RequestOrigin.TITLE,
    RequestOrigin.SUMMARY,
    RequestOrigin.STRUCTURED_CLASSIFICATION,
)


@dataclass(frozen=True)
class CacheSettings:
    """[llm.cache]（ADR-0012 §3）：响应缓存开关与参数。默认关闭。"""

    enabled: bool = False
    ttl_seconds: float | None = 600.0
    max_entries: int = 256
    origins: tuple[RequestOrigin, ...] = _DEFAULT_CACHE_ORIGINS


@dataclass(frozen=True)
class CircuitBreakerSettings:
    """[llm.circuit_breaker]（ADR-0012 §8）：健康熔断。默认关闭。"""

    enabled: bool = False
    failure_threshold: int = 5
    cooldown_seconds: float = 30.0


@dataclass(frozen=True)
class RetrySettings:
    """[llm.retry]（ADR-0012 §2）：429 短等重试阈值（秒）。"""

    wait_threshold_seconds: float = 5.0


@dataclass(frozen=True)
class LlmConfig:
    """整份有效 LLM 配置（只读视图）。"""

    providers: tuple[ProviderConfig, ...]

    def provider(self, provider_id: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.id == provider_id), None)

    def model(self, provider_id: str, model_id: str) -> ModelSpec | None:
        provider = self.provider(provider_id)
        return provider.model(model_id) if provider else None

    def all_models(self) -> tuple[ModelSpec, ...]:
        return tuple(m for p in self.providers for m in p.models)
