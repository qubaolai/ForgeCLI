"""mock供应商 FakeModelProvider（ADR-0011 §17 测试策略）。

用于单测与本地闭环：让 gateway / 后续 AgentTurn 集成不依赖真实网络或真实模型。
契约（ModelProvider）：只做协议映射，不读环境变量、不读配置文件、不做预算决策。

可注入三类行为，覆盖验收要求的三种测试场景：
    - 固定文本回复（成功）。
    - 注入 usage（None 表示供应商未返回，交由 gateway 估算并标记 estimated）。
    - 注入 provider 错误（complete 抛出，交由 gateway 归一化）。

默认冒充一个已知 provider（deepseek），从而无需往封闭集里塞测试专用 provider id。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from forgecli.application.llm.gateway.messages import ToolCall
from forgecli.application.llm.gateway.provider import (
    ModelProvider,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResponse,
)
from forgecli.application.llm.gateway.response import FinishReason, ModelUsage


class FakeModelProvider(ModelProvider):
    """内存态、mock的 ModelProvider"""

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
    ) -> None:
        self._provider_id = provider_id
        self._content = content
        self._usage = usage
        self._finish_reason = finish_reason
        self._tool_calls = tool_calls
        self._error = error
        self._capabilities = capabilities or ProviderCapabilities()
        # 转不可变视图
        self._raw_metadata: Mapping[str, str] = MappingProxyType(
            dict(raw_metadata) if raw_metadata else {}
        )

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        if self._error is not None:
            raise self._error

        return ProviderResponse(
            content=self._content,
            finish_reason=self._finish_reason,
            tool_calls=self._tool_calls,
            usage=self._usage,
            raw_metadata=self._raw_metadata,
        )
