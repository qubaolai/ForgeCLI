"""确定性假供应商 FakeModelProvider（ADR-0011 §17 测试策略）。

用于单测与本地闭环：让 gateway / AgentTurn 集成不依赖真实网络或真实模型。
契约（ModelProvider）：只做协议映射，不读环境变量、不读配置文件、不做预算决策。

可注入行为，覆盖验收要求的测试场景：
    - 固定文本回复（成功）。
    - 注入 usage（None 表示供应商未返回，交由 gateway 估算并标记 estimated）。
    - 注入 provider 错误（complete / stream 抛出，交由 gateway 归一化）。
    - 脚本化 stream chunks（§9 流式归一化测试）。
    - 协作式取消（§8）：complete / stream 检查 request.cancel_token，
      已取消则抛 ModelCancelledError，模拟中止在途请求。

最近一次收到的 ProviderRequest 保存在 last_request，供测试断言凭证注入 /
schema 透传 / 超时合并等协议映射输入。

默认冒充一个已知 provider（deepseek），从而无需往封闭集里塞测试专用 provider id。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import MappingProxyType

from forgecli.application.llm.gateway.errors import ModelCancelledError
from forgecli.application.llm.gateway.provider import (
    ModelProvider,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResponse,
)
from forgecli.application.llm.gateway.streaming import ProviderStreamChunk
from forgecli.domain.model.response import FinishReason, ModelUsage
from forgecli.domain.tool.tool_call import ToolCall


class FakeModelProvider(ModelProvider):
    """内存态、确定性的 ModelProvider 测试替身。"""

    def __init__(
        self,
        *,
        provider_id: str = "deepseek",
        content: str = "",
        usage: ModelUsage | None = None,
        finish_reason: FinishReason = FinishReason.STOP,
        tool_calls: tuple[ToolCall, ...] = (),
        error: Exception | None = None,
        capabilities: ProviderCapabilities | None = None,
        raw_metadata: Mapping[str, str] | None = None,
        stream_chunks: tuple[ProviderStreamChunk, ...] = (),
    ) -> None:
        self._provider_id = provider_id
        self._content = content
        self._usage = usage
        self._finish_reason = finish_reason
        self._tool_calls = tool_calls
        self._error = error
        self._capabilities = capabilities or ProviderCapabilities()
        self._raw_metadata: Mapping[str, str] = MappingProxyType(
            dict(raw_metadata) if raw_metadata else {}
        )
        self._stream_chunks = stream_chunks
        self.last_request: ProviderRequest | None = None
        self.complete_calls = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.last_request = request
        self.complete_calls += 1
        self._raise_if_cancelled(request)
        if self._error is not None:
            raise self._error
        return ProviderResponse(
            content=self._content,
            finish_reason=self._finish_reason,
            tool_calls=self._tool_calls,
            usage=self._usage,
            raw_metadata=self._raw_metadata,
        )

    def stream(self, request: ProviderRequest) -> Iterator[ProviderStreamChunk]:
        self.last_request = request
        self._raise_if_cancelled(request)
        if self._error is not None:
            raise self._error
        for chunk in self._stream_chunks:
            # 协作式取消：每个 chunk 前检查取消信号，模拟中止在途 SSE（§9）。
            self._raise_if_cancelled(request)
            yield chunk

    @staticmethod
    def _raise_if_cancelled(request: ProviderRequest) -> None:
        token = request.cancel_token
        if token is not None and token.cancelled:
            raise ModelCancelledError("调用已被取消（fake provider 协作式取消）")
