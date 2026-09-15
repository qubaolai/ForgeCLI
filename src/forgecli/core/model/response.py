"""统一返回结构 ModelResponse / ModelUsage / StructuredModelResponse

usage 必须归一化：供应商没返回 usage 时由 gateway 估算并标记 estimated=true，
后续成本统计要区分真实用量与估算用量。

raw_metadata 只保存安全摘要（供应商 request id、region、cache hit），不存完整原始响应；
cached=true 表示来自 gateway 响应缓存，其真实 usage 记为 0。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from forgecli.core.tool.tool_call import ToolCall


class FinishReason(Enum):
    """归一化结束原因。user_cancelled 用于取消 / Ctrl-C。"""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    USER_CANCELLED = "user_cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class ModelUsage:
    """统一 token 用量; 所有 provider 必须归一化到这里.

    口径只在这里定一次, 按 OpenAI Chat Completions 的 `usage` 形状:

    - `cached_input_tokens` 是 `input_tokens` 的**子集**
      (`prompt_tokens_details.cached_tokens` ⊂ `prompt_tokens`), 不是另加的一笔;
    - `reasoning_tokens` 是 `output_tokens` 的**子集**
      (`completion_tokens_details.reasoning_tokens` ⊂ `completion_tokens`);
    - `total_tokens == input_tokens + output_tokens`; 供应商没回 total 时为 0, 消费方按
      两者之和补.

    消费方 (成本估算, 事件载荷, 终端与网页的用量展示) 都以此为准. 早先这里没写口径,
    于是成本估算把 reasoning 当成 output 之外的加项, 而网页把 cached 与 input 并排当成
    两笔 —— 同一组数字, 三处三种读法. 接一个口径不同的供应商 (比如把 cache read 与 input
    分开报的) 时, 在它的适配器里换算成这个形状, 不要改这里的定义.
    """

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负")


@dataclass(frozen=True)
class ModelResponse:
    """非流式 / 流式收尾的统一返回。"""

    request_id: str
    provider: str
    model: str
    content: str
    finish_reason: FinishReason
    usage: ModelUsage
    latency_ms: float
    tool_calls: tuple[ToolCall, ...] = ()
    cached: bool = False
    raw_metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("ModelResponse.request_id 不能为空")
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("ModelResponse 必须带 provider 与 model")
        if self.latency_ms < 0:
            raise ValueError("latency_ms 不能为负")


@dataclass(frozen=True)
class StructuredModelResponse:
    """结构化输出的统一返回。

    data 为通过 schema 校验后的结构化数据；校验失败时 data 为 None，
    validation_errors 记录归一化后的校验错误（上层据此归一化为 parse error）。
    """

    request_id: str
    provider: str
    model: str
    data: object | None
    usage: ModelUsage
    latency_ms: float
    validation_errors: tuple[str, ...] = ()
    raw_metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("StructuredModelResponse.request_id 不能为空")
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("StructuredModelResponse 必须带 provider 与 model")
