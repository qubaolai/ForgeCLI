"""端口形状自洽：用进程内 fake 证明 LlmGateway / ModelProvider 可实现且契约一致。

这些 fake 只活在测试里（真正的 FakeModelProvider 是后续切片的 infrastructure 模块）。
它们也顺带验收：provider adapter 只做协议映射，不接触 session / state / event store
——fake 的依赖里根本没有这些 store。
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    FinishReason,
    LlmGateway,
    ModelParams,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
    ModelUsage,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResponse,
    RequestOrigin,
    StructuredModelRequest,
    StructuredModelResponse,
    TextBlock,
)
from forgecli.domain.conversation import MessageRole


class _FakeProvider(ModelProvider):
    @property
    def provider_id(self) -> str:
        return "fake"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_tools=True)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            content=f"echo:{request.model}", finish_reason=FinishReason.STOP
        )


class _FakeGateway(LlmGateway):
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    def complete(self, request: ModelRequest) -> ModelResponse:
        # 网关把统一请求降解为 adapter 的协议映射输入（此处用占位 model）。
        provider_resp = self._provider.complete(
            ProviderRequest(
                model="fake-model",
                messages=request.messages,
                params=request.params,
            )
        )
        # usage 缺失时由网关估算并标记 estimated（§3.8 行为，这里给个占位实现）。
        usage = provider_resp.usage or ModelUsage(
            input_tokens=1, output_tokens=1, estimated=True
        )
        return ModelResponse(
            request_id=request.request_id,
            provider=self._provider.provider_id,
            model="fake-model",
            content=provider_resp.content,
            finish_reason=provider_resp.finish_reason,
            usage=usage,
            latency_ms=0.0,
        )

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        return StructuredModelResponse(
            request_id=request.model_request.request_id,
            provider=self._provider.provider_id,
            model="fake-model",
            data={"ok": True},
            usage=ModelUsage(input_tokens=1, output_tokens=1, estimated=True),
            latency_ms=0.0,
        )

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        # 逐块产出文本，末块补 usage + finish_reason（§9：末块必须能汇总）。
        content = self._provider.complete(
            ProviderRequest(
                model="fake-model",
                messages=request.messages,
                params=request.params,
            )
        ).content
        for sequence, char in enumerate(content):
            yield ModelStreamChunk(
                request_id=request.request_id,
                sequence=sequence,
                provider="fake",
                model="fake-model",
                delta_text=char,
            )
        yield ModelStreamChunk(
            request_id=request.request_id,
            sequence=len(content),
            provider="fake",
            model="fake-model",
            usage_delta=ModelUsage(input_tokens=1, output_tokens=1, estimated=True),
            finish_reason=FinishReason.STOP,
        )


def _request() -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )


def test_gateway_complete_returns_normalized_response() -> None:
    gw = _FakeGateway(_FakeProvider())
    resp = gw.complete(_request())
    assert resp.request_id == "req_1"
    assert resp.provider == "fake"
    assert resp.content == "echo:fake-model"
    assert resp.usage.estimated is True  # provider 未给 usage -> 网关估算


def test_gateway_complete_structured_returns_data() -> None:
    gw = _FakeGateway(_FakeProvider())
    structured = StructuredModelRequest(
        model_request=_request(),
        schema={"type": "object"},
        schema_name="demo",
    )
    resp = gw.complete_structured(structured)
    assert resp.data == {"ok": True}


def test_gateway_stream_is_part_of_the_port_and_summarizes_on_last_chunk() -> None:
    # stream 是 LlmGateway 的 abstract 成员：网关实现不能只提供非流式入口。
    assert "stream" in LlmGateway.__abstractmethods__
    chunks = list(_FakeGateway(_FakeProvider()).stream(_request()))
    assert "".join(c.delta_text or "" for c in chunks) == "echo:fake-model"
    assert [c.sequence for c in chunks] == list(range(len(chunks)))
    last = chunks[-1]
    assert last.finish_reason is FinishReason.STOP
    assert last.usage_delta is not None


def test_provider_signature_takes_only_protocol_request() -> None:
    # adapter.complete 只吃 ProviderRequest（协议映射输入），不吃 ModelRequest /
    # session / store——从签名层面保证它不触碰网关治理状态与落盘边界。
    # 本模块启用 PEP 563（from __future__ import annotations），注解为字符串形式。
    sig = inspect.signature(_FakeProvider.complete)
    params = [p.annotation for p in sig.parameters.values() if p.name != "self"]
    assert params == ["ProviderRequest"]
