"""统一超参 ModelParams 与 ThinkingConfig（ADR-0011 §3.5）。

参数分三类：通用超参、thinking 配置、provider 私有选项。

    - 通用超参（temperature/top_p/max_output_tokens/stop/response_format）由网关校验。
    - thinking 是 ForgeCLI 的统一抽象，由 provider adapter 翻译成各供应商字段；
      enabled=auto 表示由 gateway 按 origin 默认策略决定，不交给 LLM。
    - provider_options 按 provider 命名空间隔离（如 provider_options["deepseek"]），
      只能由对应 provider adapter 读取，不参与参数合并。

注意：本类是*调用切片*的请求超参，与 application/llm/config 里同名的
ModelParams（配置切片：模型元数据 + 计费 + 标准超参）是两个不同对象，分属不同命名空间，
不在 application/llm 顶层 __init__ 同时 re-export，避免命名冲突。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType


class ThinkingMode(Enum):
    """是否开启思考。auto 表示由 gateway 按 origin 默认策略决定（ADR §3.5）。"""

    ON = "on"
    OFF = "off"
    AUTO = "auto"


class ThinkingEffort(Enum):
    """思考强度档位。与 budget_tokens 互为高低层表达，两者都给时以 effort 为准。"""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ThinkingConfig:
    """统一思考配置；provider adapter 负责翻译为供应商字段。"""

    enabled: ThinkingMode = ThinkingMode.AUTO
    effort: ThinkingEffort = ThinkingEffort.NONE
    budget_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.budget_tokens is not None and self.budget_tokens <= 0:
            raise ValueError("ThinkingConfig.budget_tokens 必须为正整数")


@dataclass(frozen=True)
class ModelParams:
    """一次调用的通用超参 + thinking + provider 私有选项。"""

    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None
    stop: tuple[str, ...] = ()
    response_format: str | None = None
    thinking: ThinkingConfig | None = None
    # provider_options: { provider_id -> { 任意私有键 } }，按命名空间整体透传。
    provider_options: Mapping[str, Mapping[str, object]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature 必须在 [0.0, 2.0] 内")
        if self.top_p is not None and not 0.0 <= self.top_p <= 1.0:
            raise ValueError("top_p 必须在 [0.0, 1.0] 内")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens 必须为正整数")
        for ns, opts in self.provider_options.items():
            if not isinstance(opts, Mapping):
                raise ValueError(f"provider_options[{ns!r}] 必须是命名空间映射")
