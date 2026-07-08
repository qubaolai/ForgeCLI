"""供应商适配接口 ModelProvider（ADR-0011 §3.2）。

ModelProvider 只描述「把统一请求映射到某供应商 API」的协议映射，约束（§2 / §3.2）：
    - 不读取 ForgeCLI session state，不写事件 / 配置，不做预算决策。
    - 不直接读任意环境变量；凭证由 CredentialResolver 传入（§7，后续切片）。
    - 抛出的错误必须被 gateway 转成统一 ModelGatewayError。

输入用 ProviderRequest 而非 ModelRequest：gateway 已解析 selection / 路由 / 预算 /
取消等*治理*字段，只把发请求所需的协议内容交给 adapter，从结构上保证 adapter 不触碰
gateway 治理状态。capabilities() 只描述 adapter/provider 级能力（协议特性、streaming /
tool schema 变体）；单模型能力（context window 等）以 ModelCatalogService 为准（§4）。

stream(...) 延后到 streaming 切片（§9）：届时新增 ProviderStreamChunk 并补到本接口，
今日不预建其 DTO（不提前创建空的远期模块）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.application.llm.gateway.messages import ChatMessage, ToolCall, ToolSpec
from forgecli.application.llm.gateway.params import ModelParams
from forgecli.application.llm.gateway.response import FinishReason, ModelUsage


@dataclass(frozen=True)
class ProviderCapabilities:
    """adapter / provider 级能力（不是单模型能力的事实来源，§3.2 / §4）。"""

    supports_streaming: bool = False
    supports_tools: bool = False
    supports_structured_output: bool = False
    supports_thinking: bool = False


@dataclass(frozen=True)
class ProviderRequest:
    """gateway 解析 selection / 路由后交给 adapter 的协议映射输入。

    只含发请求所需内容；不含 selection / budget_snapshot / cancel_token 等治理字段。
    model 为*已解析*的具体模型 id。
    """

    model: str
    messages: tuple[ChatMessage, ...]
    params: ModelParams
    system_prompt: str | None = None
    tools: tuple[ToolSpec, ...] = ()

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("ProviderRequest.model 不能为空")


@dataclass(frozen=True)
class ProviderResponse:
    """adapter 返回的供应商归一化结果；gateway 再补 request_id 等收尾成 ModelResponse。

    usage 可为 None（供应商未返回）；此时由 gateway 估算并标记 estimated（§3.8）。
    raw_metadata 只保存安全摘要，不含完整原始响应、不含凭证。
    """

    content: str
    finish_reason: FinishReason
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage | None = None
    raw_metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )


class ModelProvider(ABC):
    """供应商的适配接口。具体实现住在 infrastructure（含未来 FakeModelProvider）。"""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """供应商 id，作为 gateway 路由的分发键。"""

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """返回 adapter / provider 级能力。"""

    @abstractmethod
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """把协议映射输入发往供应商并返回归一化结果。"""

    # stream(request: ProviderRequest) -> Iterator[ProviderStreamChunk]:
    #     延后到 streaming 切片（ADR §9），今日不冻结其 chunk DTO。
