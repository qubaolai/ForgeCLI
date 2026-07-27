"""统一 LLM 调用网关端口 LlmGateway（ADR-0011 §3.1）。

LlmGateway 是所有模型调用的*唯一*入口。AgentLoop 只依赖本端口，不依赖具体 provider
adapter，也不 import 任何供应商 SDK（§19 验收）。具体实现住在后续切片。

职责（§3.1，今日仅冻结端口形状，不实现）：解析 current_model / explicit_model、
校验模型可用性与策略、注入默认超参、路由 provider/model、请求前估算 token 与预算、
调用 provider adapter、归一化 usage / finish reason / 错误，返回 usage 草稿交
AgentTurnService 落盘。

边界：gateway 不直接写 events.jsonl / state.json / usage 文件 / 日志原文（§2 / §8）。

stream(...) -> Iterator[ModelStreamChunk]：延后到 streaming 切片（§9）。今日只冻
complete / complete_structured 两个契约；stream 的 chunk DTO 不提前创建。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from forgecli.application.llm.gateway.request import (
    ModelRequest,
    StructuredModelRequest,
)
from forgecli.application.llm.gateway.streaming import ModelStreamChunk
from forgecli.domain.model.response import (
    ModelResponse,
    StructuredModelResponse,
)


class LlmGateway(ABC):
    """所有模型调用的统一入口端口。"""

    @abstractmethod
    def complete(self, request: ModelRequest) -> ModelResponse:
        """非流式补全：返回归一化的 ModelResponse（含 usage 草稿）。"""

    @abstractmethod
    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        """结构化输出：按 schema 校验，失败归一化为 ModelResponseParseError。"""

    @abstractmethod
    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        """流式输出：产出统一 ModelStreamChunk（§9）。

        末块必须携带 usage_delta 与 finish_reason；取消 / 中断时产出
        interrupted=True 的收尾块，而不是直接断流。
        """
