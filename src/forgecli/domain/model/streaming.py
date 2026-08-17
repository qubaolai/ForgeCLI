"""流式返回的增量块词汇（ADR-0011 §9）。

三个 chunk 描述的是一次流式调用会吐出什么形状的增量, 供应商适配器负责把各家 SDK
的流翻译成它们。累积器 StreamAccumulator 是可变的运行时构件, 留在 application。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.model.response import FinishReason, ModelUsage


@dataclass(frozen=True)
class ToolCallDelta:
    """一段工具调用增量（供应商流式返回的 tool call 片段）。

    index 标识同一响应内的第几个工具调用；tool_call_id / name 只在首个片段出现，
    arguments_delta 为 JSON 文本增量，累积完整后才解析。
    """

    index: int
    tool_call_id: str | None = None
    name: str | None = None
    arguments_delta: str = ""

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("ToolCallDelta.index 不能为负")


@dataclass(frozen=True)
class ProviderStreamChunk:
    """adapter 归一化后的供应商流式片段；gateway 再补 request_id / 序号。

    tool_call_deltas 是**列表**: 一个供应商 chunk 可以同时携带多个工具调用的片段
    (OpenAI 兼容协议的 `delta.tool_calls` 本身就是数组). 只保留首项会让并行工具调用
    整体消失, 模型随后收到残缺的结果集 —— 那是执行正确性问题, 不是展示问题.
    """

    delta_text: str | None = None
    tool_call_deltas: tuple[ToolCallDelta, ...] = ()
    usage: ModelUsage | None = None
    finish_reason: FinishReason | None = None


@dataclass(frozen=True)
class ModelStreamChunk:
    """gateway 输出的统一流式片段（§9）。sequence 从 0 递增。

    provider/model 为 gateway 已解析的模型身份：每块统一携带，收尾块因此
    自足构成完整 ModelResponse 汇总（§9），消费方（如 usage 计量）无需回查选择器。
    """

    request_id: str
    sequence: int
    provider: str
    model: str
    delta_text: str | None = None
    tool_call_deltas: tuple[ToolCallDelta, ...] = ()
    usage_delta: ModelUsage | None = None
    finish_reason: FinishReason | None = None
    interrupted: bool = False

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("ModelStreamChunk.request_id 不能为空")
        if self.sequence < 0:
            raise ValueError("ModelStreamChunk.sequence 不能为负")
        if not self.provider.strip():
            raise ValueError("ModelStreamChunk.provider 不能为空")
        if not self.model.strip():
            raise ValueError("ModelStreamChunk.model 不能为空")
