"""供应商适配接口 ModelProvider（ADR-0011 §3.2）。

ModelProvider 只描述「把统一请求映射到某供应商 API」的协议映射，约束（§2 / §3.2）：
    - 不读取 ForgeCLI session state，不写事件 / 配置，不做预算决策。
    - 不直接读任意环境变量；凭证由 CredentialResolver 传入（§7，后续切片）。
    - 抛出的错误必须被 gateway 转成统一 ModelGatewayError。

输入用 ProviderRequest 而非 ModelRequest：gateway 已解析 selection / 路由 / 预算 /
取消等*治理*字段，只把发请求所需的协议内容交给 adapter，从结构上保证 adapter 不触碰
gateway 治理状态。capabilities() 只描述 adapter/provider 级能力（协议特性、streaming /
tool schema 变体）；单模型能力（context window 等）以 ModelCatalogService 为准（§4）。

stream(...) 返回统一 ProviderStreamChunk（§9）；未覆写视为不支持 streaming，
gateway 先查 capabilities().supports_streaming 再路由流式调用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.application.llm.gateway.credentials import Credential
from forgecli.application.llm.gateway.request import CancelToken
from forgecli.application.llm.gateway.streaming import ProviderStreamChunk
from forgecli.domain.conversation.message import ChatMessage
from forgecli.domain.model.params import ModelParams, ThinkingConfig
from forgecli.domain.model.response import FinishReason, ModelUsage
from forgecli.domain.tool.tool_call import ToolCall, ToolSpec


@dataclass(frozen=True)
class ProviderCapabilities:
    """adapter / provider 级能力（不是单模型能力的事实来源，§3.2 / §4）。"""

    supports_streaming: bool = False
    supports_tools: bool = False
    supports_structured_output: bool = False


@dataclass(frozen=True)
class ProviderRequest:
    """gateway 解析 selection / 路由后交给 adapter 的协议映射输入。

    只含发请求所需内容；不含 selection / budget_snapshot 等*决策*字段。
    model 为*已解析*的具体模型 id。

    §7 / §8 要求下发到 adapter 的三个执行要素（07-03 起补入）：
        - credential：由 gateway 经 CredentialPool 租借后注入；adapter 不读环境变量、
          不枚举或切换 key。keyless provider（如 local）为 None。
        - cancel_token：取消信号传播到 adapter，由 adapter 中止在途 HTTP 请求，
          不靠等待自然超时。
        - timeout_seconds：gateway 按「请求级 > provider 默认」合并后的超时。

    结构化输出（§3.7 native 通道）：response_schema / schema_name / strict_schema
    由 complete_structured 注入；普通 complete 调用为 None。
    """

    model: str
    messages: tuple[ChatMessage, ...]
    params: ModelParams
    # gateway 根据最终解析出的具体模型配置生成；ModelRequest 不可显式指定。
    thinking: ThinkingConfig | None = None
    system_prompt: str | None = None
    tools: tuple[ToolSpec, ...] = ()
    timeout_seconds: float | None = None
    cancel_token: CancelToken | None = None
    credential: Credential | None = None
    response_schema: Mapping[str, object] | None = None
    schema_name: str | None = None
    strict_schema: bool = True

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("ProviderRequest.model 不能为空")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("ProviderRequest.timeout_seconds 必须为正数")


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

    def stream(self, request: ProviderRequest) -> Iterator[ProviderStreamChunk]:
        """流式调用：把供应商私有 SSE/event 转成统一 ProviderStreamChunk（§9）。

        以具体方法加入端口（06-29 冻结契约只含 complete），未覆写视为不支持
        streaming；gateway 会先查 capabilities().supports_streaming 再调用。
        取消经 request.cancel_token 传播：adapter 收到取消须中止底层连接并停止
        产出 chunk，不等待自然超时。
        """
        raise NotImplementedError("该 ModelProvider 实现不支持 stream")
