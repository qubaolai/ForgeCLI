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

import os
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
    # 运行期日志级别 (ADR-0035). 它是**这份视图里唯一一个在进程启动之前就要读到的键**:
    # 日志装配发生在任何 service 之前, 所以那条路径直接读 config.json, 不经这里.
    # 收进来是为了 /config 能显示与修改它 —— 一个只能改文件的开关等于没有开关.
    logging_level: str
    default_model: ModelRef | None  # 未配置时为 None
    # 额外进受控 PATH 的工具链目录 (ADR-0014 §4.2). 探测出来的候选目录只有那几个系统
    # 位置, 装在别处的 maven / jdk / node 因此在受控 PATH 上根本不存在 —— 模型跑不了
    # 构建, 也就永远验证不了自己写的代码能不能起来.
    #
    # 它们进 PATH 但**不算可信**: ExecutableResolver 把它们判成 TOOLCHAIN, 里面的
    # 可执行文件仍然按脚本执行分析.
    toolchain_dirs: tuple[str, ...]

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
            default_model=_read_model(overrides),
            toolchain_dirs=_read_toolchain_dirs(
                value(config_keys.EXECUTION_TOOLCHAIN_DIRS)
            ),
        )

    def as_dict(self) -> dict[str, str]:
        """键 -> 规范字符串，供菜单展示与序列化对比。"""
        return {
            config_keys.TELEMETRY_ENABLED: "true"
            if self.telemetry_enabled
            else "false",
            config_keys.OUTPUT_THEME: self.output_theme,
            config_keys.LOGGING_LEVEL: self.logging_level,
            config_keys.EXECUTION_TOOLCHAIN_DIRS: os.pathsep.join(self.toolchain_dirs),
        }

    def display(self, key: str) -> str:
        """某个键当前的有效取值（字符串），未知键回 "(未知)"。"""
        return self.as_dict().get(key, "(未知)")


def _read_toolchain_dirs(raw: str) -> tuple[str, ...]:
    """按 os.pathsep 拆开, 与 PATH 本身同一种写法.

    分隔符取平台的而不是固定一个字符: Windows 路径里的 `C:` 会把冒号分法切坏, 而这份
    值最终就是要拼进 PATH 的.
    """
    return tuple(entry for entry in raw.split(os.pathsep) if entry.strip())


def _read_model(overrides: Mapping[str, str]) -> ModelRef | None:
    """从扁平覆盖项读取默认模型；两个 id 任一缺失即视为未配置。"""
    provider = (overrides.get(config_keys.DEFAULT_MODEL_PROVIDER_KEY) or "").strip()
    model = (overrides.get(config_keys.DEFAULT_MODEL_NAME_KEY) or "").strip()
    if not provider or not model:
        return None
    return ModelRef(provider=provider, model=model)
