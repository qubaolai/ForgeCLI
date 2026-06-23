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

# 标准化字段名（出现在模型行内表里、且我们认识的键）。其余键归入 extra。
_KNOWN_FIELDS = {
    "context_window",
    "max_tokens",
    "cost_per_1k_input",
    "cost_per_1k_output",
    "temperature",
    "top_p",
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


@dataclass(frozen=True)
class StandardField:
    """一个标准化模型字段的 UI / 解析元数据。"""

    name: str
    label: str
    kind: type  # int | float


# 可在交互式菜单里逐项编辑的标准字段（顺序即菜单顺序）。extra 走单独的 JSON 编辑。
STANDARD_FIELDS: tuple[StandardField, ...] = (
    StandardField("context_window", "上下文窗口", int),
    StandardField("max_tokens", "最大输出 tokens", int),
    StandardField("temperature", "温度", float),
    StandardField("top_p", "top_p", float),
    StandardField("cost_per_1k_input", "输入价格/1k", float),
    StandardField("cost_per_1k_output", "输出价格/1k", float),
)

_FIELD_KIND: dict[str, type] = {f.name: f.kind for f in STANDARD_FIELDS}


def coerce_field(name: str, raw: str) -> int | float:
    """把菜单文本输入转成字段的原生类型；不能解析时抛 ConfigValidationError。

    只做类型转换，范围校验仍由 ModelParams.parse 统一负责。
    """
    kind = _FIELD_KIND.get(name)
    if kind is None:
        raise ConfigValidationError(f"未知模型字段: {name}")
    text = raw.strip()
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
    temperature: float | None = None
    top_p: float | None = None
    extra: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def to_fields(self) -> dict[str, object]:
        """转回可写入 TOML 的字段 dict（只含已设置的项），供读-改-写复用。"""
        out: dict[str, object] = {}
        if self.context_window is not None:
            out["context_window"] = self.context_window
        if self.max_tokens is not None:
            out["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            out["temperature"] = self.temperature
        if self.top_p is not None:
            out["top_p"] = self.top_p
        if self.cost_per_1k_input:
            out["cost_per_1k_input"] = self.cost_per_1k_input
        if self.cost_per_1k_output:
            out["cost_per_1k_output"] = self.cost_per_1k_output
        if self.extra:
            out["extra"] = dict(self.extra)
        return out

    def extra_json(self) -> str:
        """extra 的单行 JSON 文本，供菜单展示与编辑初值。"""
        return json.dumps(dict(self.extra), ensure_ascii=False) if self.extra else ""

    @classmethod
    def parse(cls, raw: Mapping[str, object]) -> ModelParams:
        """从配置行内表构造；非法值抛 ConfigValidationError。"""
        cw = raw.get("context_window")
        mt = raw.get("max_tokens")
        temp = raw.get("temperature")
        top_p = raw.get("top_p")
        extra = raw.get("extra", {})

        if not isinstance(extra, Mapping):
            raise ConfigValidationError("extra 必须是 JSON 对象（键值表）")

        # 未在标准字段列表里的多余键，宽容地并入 extra，便于厂商自定义透传。
        merged_extra = {k: v for k, v in raw.items() if k not in _KNOWN_FIELDS}
        merged_extra.update(extra)

        return cls(
            context_window=None
            if cw is None
            else _as_positive_int("context_window", cw),
            max_tokens=None if mt is None else _as_positive_int("max_tokens", mt),
            cost_per_1k_input=_as_nonneg_float(
                "cost_per_1k_input", raw.get("cost_per_1k_input", 0.0)
            ),
            cost_per_1k_output=_as_nonneg_float(
                "cost_per_1k_output", raw.get("cost_per_1k_output", 0.0)
            ),
            temperature=None
            if temp is None
            else _as_ranged_float("temperature", temp, 0.0, 2.0),
            top_p=None if top_p is None else _as_ranged_float("top_p", top_p, 0.0, 1.0),
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

    def model(self, model_id: str) -> ModelSpec | None:
        return next((m for m in self.models if m.id == model_id), None)


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
