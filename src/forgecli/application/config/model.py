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

from forgecli.application.config import keys

_DEFAULTS: dict[str, str] = {key.name: key.default for key in keys.SCHEMA}


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EffectiveConfig:
    """合并默认值与用户覆盖后的有效配置快照。"""

    workspace_dir: str
    telemetry_enabled: bool
    output_theme: str
    log_level: str

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, str]) -> EffectiveConfig:
        """用覆盖项构造有效配置；缺失的键回落默认值，未知键被忽略。"""

        def value(name: str) -> str:
            raw = overrides.get(name)
            return raw if raw is not None else _DEFAULTS[name]

        return cls(
            workspace_dir=value(keys.WORKSPACE_DIR),
            telemetry_enabled=_as_bool(value(keys.TELEMETRY_ENABLED)),
            output_theme=value(keys.OUTPUT_THEME),
            log_level=value(keys.LOG_LEVEL),
        )

    def as_dict(self) -> dict[str, str]:
        """键 -> 规范字符串，供菜单展示与序列化对比。"""
        return {
            keys.WORKSPACE_DIR: self.workspace_dir,
            keys.TELEMETRY_ENABLED: "true" if self.telemetry_enabled else "false",
            keys.OUTPUT_THEME: self.output_theme,
            keys.LOG_LEVEL: self.log_level,
        }

    def display(self, key: str) -> str:
        """某个键当前的有效取值（字符串），未知键回 "(未知)"。"""
        return self.as_dict().get(key, "(未知)")
