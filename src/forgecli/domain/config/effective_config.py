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
from forgecli.domain.execution.environment import EnvironmentInheritance
from forgecli.domain.model.model_ref import ModelRef

_DEFAULTS: dict[str, str] = {key.name: key.default for key in config_keys.SCHEMA}


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _text(value: bool) -> str:
    return "true" if value else "false"


@dataclass(frozen=True)
class EffectiveConfig:
    """合并默认值与用户覆盖后的有效配置快照。"""

    # 是否采集进程内的阶段耗时与计数 (shared/observability/metrics). 关掉之后
    # /diagnostics 的 stages 是空的, 记录点本身变成空操作.
    telemetry_enabled: bool
    output_theme: str
    # 日志装配 (ADR-0035). 这几个键**在进程启动之前就要读到**: 日志装配发生在任何
    # service 之前, 所以那条路径直接读 config.json, 不经这份视图.
    # 收进来是为了 /config 能显示与修改它们 —— 一个只能改文件的开关等于没有开关.
    logging_level: str
    logging_console: bool
    logging_directory: str
    logging_max_value_chars: str
    logging_include_http: bool
    default_model: ModelRef | None  # 未配置时为 None
    # 从启动 shell 继承多少环境. 默认全继承 —— 等价性是主要需求, 见
    # `domain/execution/environment` 的模块说明. 收紧只在明确要求跨机器可复现时才有意义.
    environment_inheritance: EnvironmentInheritance

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, str]) -> EffectiveConfig:
        """用覆盖项构造有效配置；缺失的键回落默认值，未知键被忽略。"""

        def value(name: str) -> str:
            raw = overrides.get(name)
            return raw if raw is not None else _DEFAULTS[name]

        return cls(
            telemetry_enabled=_as_bool(value(config_keys.TELEMETRY_ENABLED)),
            output_theme=value(config_keys.OUTPUT_THEME),
            logging_level=value(config_keys.LOGGING_LEVEL),
            logging_console=_as_bool(value(config_keys.LOGGING_CONSOLE)),
            logging_directory=value(config_keys.LOGGING_DIRECTORY),
            logging_max_value_chars=value(config_keys.LOGGING_MAX_VALUE_CHARS),
            logging_include_http=_as_bool(value(config_keys.LOGGING_INCLUDE_HTTP)),
            default_model=_read_model(overrides),
            environment_inheritance=_read_inheritance(
                value(config_keys.EXECUTION_ENV_INHERIT)
            ),
        )

    def as_dict(self) -> dict[str, str]:
        """键 -> 规范字符串，供菜单展示与序列化对比。"""
        return {
            config_keys.TELEMETRY_ENABLED: _text(self.telemetry_enabled),
            config_keys.OUTPUT_THEME: self.output_theme,
            config_keys.LOGGING_LEVEL: self.logging_level,
            config_keys.LOGGING_CONSOLE: _text(self.logging_console),
            config_keys.LOGGING_DIRECTORY: self.logging_directory,
            config_keys.LOGGING_MAX_VALUE_CHARS: self.logging_max_value_chars,
            config_keys.LOGGING_INCLUDE_HTTP: _text(self.logging_include_http),
            config_keys.EXECUTION_ENV_INHERIT: self.environment_inheritance.value,
        }

    def display(self, key: str) -> str:
        """某个键当前的有效取值（字符串），未知键回 "(未知)"。"""
        return self.as_dict().get(key, "(未知)")


def _read_inheritance(raw: str) -> EnvironmentInheritance:
    """认不出的取值回落到默认档, 不抛异常.

    这是配置读取路径, 抛异常的后果是一个手滑的取值让整个会话起不来; 而回落到默认档
    的后果只是"我配的没生效", 而 `as_dict` 会把真实取值显示出来.
    """
    try:
        return EnvironmentInheritance(raw.strip().casefold())
    except ValueError:
        return EnvironmentInheritance.ALL


def _read_model(overrides: Mapping[str, str]) -> ModelRef | None:
    """从扁平覆盖项读取默认模型；两个 id 任一缺失即视为未配置。"""
    provider = (overrides.get(config_keys.DEFAULT_MODEL_PROVIDER_KEY) or "").strip()
    model = (overrides.get(config_keys.DEFAULT_MODEL_NAME_KEY) or "").strip()
    if not provider or not model:
        return None
    return ModelRef(provider=provider, model=model)
