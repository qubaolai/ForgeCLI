"""统一 LLM 网关的 MVP 实现 DefaultLlmGateway（ADR-0011 §3.1 / §17）。

当前只落地 complete 的最小 happy path：仅支持 explicit selection，
按已解析 provider/model 路由到 adapter，归一化返回 ModelResponse。
tier / current_model 路由、credential 解析、streaming、tool calling
执行、结构化输出校验均为后续切片。

边界：网关不直接写 events / state / usage 文件；usage 只作为草稿随 ModelResponse 返回，
由 AgentTurnService 落盘。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from forgecli.application.llm.gateway.errors import (
    ModelBadRequestError,
    ModelGatewayError,
    ModelProviderInternalError,
)
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.provider import ProviderRequest, ProviderResponse
from forgecli.application.llm.gateway.provider_registry import ProviderRegistry
from forgecli.application.llm.gateway.request import (
    ModelRequest,
    StructuredModelRequest,
)
from forgecli.application.llm.gateway.response import (
    ModelResponse,
    ModelUsage,
    StructuredModelResponse,
)
from forgecli.application.llm.gateway.selection import (
    ExplicitModelSelection,
    ModelSelection,
)
from forgecli.application.llm.model_ref import ModelRef


class DefaultLlmGateway(LlmGateway):
    """网关 MVP 实现：解析 explicit selection、路由 adapter、归一化响应。"""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        timer: Callable[[], float] = time.monotonic,
        validate_model: Callable[[ModelRef], None] | None = None,
    ) -> None:
        self._registry = registry
        # 注入计时器：让 latency_ms 在测试中可钉死
        # （沿用 SessionService 注入 clock 的模式）。
        self._timer = timer
        # 为后续路由预留的 catalog 校验入口（07-01 注入）；今日默认 no-op。
        self._validate_model = validate_model

    def complete(self, request: ModelRequest) -> ModelResponse:
        ref = self._resolve_selection(request.model_selection)
        provider = self._registry.get(ref.provider)
        provider_request = ProviderRequest(
            model=ref.model,
            messages=request.messages,
            params=request.params,
            system_prompt=request.system_prompt,
            tools=request.tools,
        )
        start = self._timer()
        try:
            provider_response = provider.complete(provider_request)
        except ModelGatewayError:
            # adapter 已归一化的网关错误：原样上抛（保留其安全上下文）。
            raise
        except Exception as exc:  # 兜底：任何非网关异常都归一化为网关错误。
            raise ModelProviderInternalError(
                f"provider {ref.provider!r} 调用失败: {exc}",
                provider=ref.provider,
                model=ref.model,
                request_id=request.request_id,
            ) from exc
        latency_ms = (self._timer() - start) * 1000.0

        usage = provider_response.usage or self._estimate_usage(
            request=request, response=provider_response
        )
        return ModelResponse(
            request_id=request.request_id,
            provider=ref.provider,
            model=ref.model,
            content=provider_response.content,
            finish_reason=provider_response.finish_reason,
            usage=usage,
            latency_ms=latency_ms,
            tool_calls=provider_response.tool_calls,
            raw_metadata=provider_response.raw_metadata,
        )

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        raise NotImplementedError(
            "complete_structured 在 07-07 结构化输出切片接线；MVP 仅实现 complete。"
        )

    def _resolve_selection(self, selection: ModelSelection) -> ModelRef:
        """把选择解析成已解析模型 ModelRef（复用既有值对象，不另建平行结构）。

        MVP 仅支持 explicit selection；current_model / tier 路由在 07-01 / 07-02 接线。
        """
        if isinstance(selection, ExplicitModelSelection):
            ref = ModelRef(provider=selection.provider, model=selection.model)
            if self._validate_model is not None:
                self._validate_model(ref)  # 预留 catalog 校验入口，今日默认 no-op
            return ref
        raise ModelBadRequestError(
            f"MVP 仅支持 explicit selection；{selection.kind.value} 路由在 "
            "07-01 / 07-02 接线。"
        )

    def _estimate_usage(
        self, request: ModelRequest, response: ProviderResponse
    ) -> ModelUsage:
        """供应商未返回 usage 时的占位估算（标记 estimated=True）。

        07-06 的 TokenEstimator 落地后替换本方法；今日仅保证 response 带
        estimated usage，隔离成独立缝以免届时改动归一化主路径。
        """
        return ModelUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated=True,
        )
