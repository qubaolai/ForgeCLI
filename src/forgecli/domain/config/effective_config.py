"""有效配置（EffectiveConfig）值对象。

EffectiveConfig 是配置的*只读视图*：把“默认值 + 用户覆盖”解析成一份带类型的
不可变快照，供 CLI / 各命令读取。它不知道值来自文件还是环境变量——多来源合并
由 ConfigService 负责，本对象只承载合并后的结果。

设计约束：
    - 不可变：frozen dataclass。
    - 类型化：bool 就是 bool，不是字符串 "true"。
    - 未知字段忽略：from_overrides 只读取 SCHEMA 中定义的键，其余一律不进入视图。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from forgecli.domain.config import config_keys
from forgecli.domain.model.model_ref import ModelRef

_DEFAULTS: dict[str, str] = {key.name: key.default for key in config_keys.SCHEMA}


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EffectiveConfig:
    """合并默认值与用户覆盖后的有效配置快照。"""

    telemetry_enabled: bool
    output_theme: str
    default_model: ModelRef | None  # 未配置时为 None

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, str]) -> EffectiveConfig:
        """用覆盖项构造有效配置；缺失的键回落默认值，未知键被忽略。"""

        def value(name: str) -> str:
            raw = overrides.get(name)
            return raw if raw is not None else _DEFAULTS[name]

        return cls(
            telemetry_enabled=_as_bool(value(config_keys.TELEMETRY_ENABLED)),
            output_theme=value(config_keys.OUTPUT_THEME),
            default_model=_read_model(overrides),
        )

    def as_dict(self) -> dict[str, str]:
        """键 -> 规范字符串，供菜单展示与序列化对比。"""
        return {
            config_keys.TELEMETRY_ENABLED: "true"
            if self.telemetry_enabled
            else "false",
            config_keys.OUTPUT_THEME: self.output_theme,
        }

    def display(self, key: str) -> str:
        """某个键当前的有效取值（字符串），未知键回 "(未知)"。"""
        return self.as_dict().get(key, "(未知)")


def _read_model(overrides: Mapping[str, str]) -> ModelRef | None:
    """从扁平覆盖项读取默认模型；两个 id 任一缺失即视为未配置。"""
    provider = (overrides.get(config_keys.DEFAULT_MODEL_PROVIDER_KEY) or "").strip()
    model = (overrides.get(config_keys.DEFAULT_MODEL_NAME_KEY) or "").strip()
    if not provider or not model:
        return None
    return ModelRef(provider=provider, model=model)
