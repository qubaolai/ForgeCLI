"""一次模型调用: 发请求, 把流式与非流式归一成同一个结果 (ADR-0010 §9 / ADR-0016 §5).

从 ``builtin_loop`` 分出来的判据是: 控制流关心"模型这次说了什么, 所以下一步做什么",
不关心它是一次性回来的还是一块一块流回来的。这里把两条路的差异 (增量外送, 中断收尾,
半截 tool call, 由收尾块合成 usage) 全部吃掉, 只交出 ``ModelOutcome`` 或 ``LoopStop``。

**它不组请求.** ``ModelRequest`` 由循环组好传进来 —— 发什么上下文是本轮的知识
(ADR-0018 §6.2: 一轮之内不隐式换前缀), 而这里只负责把它发出去。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from forgecli.application.agent_loop.run_events import LoopEventPublisher
from forgecli.application.llm.gateway.errors import (
    MalformedToolCallError,
    ModelBadRequestError,
)
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.streaming import StreamAccumulator
from forgecli.application.llm.metering import UsageMeter
from forgecli.application.llm.transport_policy import ModelTransportMode
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.agent.actions import LoopStop
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.model.request import ModelRequest
from forgecli.domain.model.response import FinishReason, ModelResponse, ModelUsage
from forgecli.domain.model.streaming import ModelStreamChunk
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.tool.tool_call import ToolCall
from forgecli.shared.observability.log import get_log

__all__ = ["AgentModelInvoker", "ModelOutcome"]

_log = get_log(__name__)


@dataclass(frozen=True)
class ModelOutcome:
    """一次模型调用的归一化产出: 文本与工具调用可以同时存在."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.text.strip() and not self.tool_calls


class AgentModelInvoker:
    """一轮之内的模型调用出口。

    ``on_usage`` 是本轮 usage 草稿的收口: 循环把压缩产生的草稿也攒在同一个列表里, 由
    AgentTurnService 统一落盘 (ADR-0037) —— 分成两份迟早会出现两套单价。
    """

    def __init__(
        self,
        gateway: LlmGateway,
        meter: UsageMeter,
        events: LoopEventPublisher,
        *,
        on_usage: Callable[[UsageRecordDraft], None],
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gateway = gateway
        self._meter = meter
        self._events = events
        self._on_usage = on_usage
        self._timer = timer
        self._partial_answer: str | None = None

    @property
    def partial_answer(self) -> str | None:
        """流式中断时已累积的部分回答文本 (无则为 None)."""
        return self._partial_answer

    def invoke(
        self,
        request: ModelRequest,
        transport: ModelTransportMode,
    ) -> ModelOutcome | LoopStop:
        """根据llm调用方式 走网关的不同接口"""
        if transport is ModelTransportMode.COMPLETE:
            return self._complete(request)
        return self._stream(request)

    # ---- 非流式 ----

    def _complete(self, request: ModelRequest) -> ModelOutcome:
        started = self._timer()
        response = self._gateway.complete(request)
        self._on_usage(self._meter.build_draft(request, response))
        # 非流式: 整段文本一次性交出, 仍然走 delta 通道, 免得终端为两种形态各写一套.
        if response.content:
            self._events.model_delta(
                request_id=request.request_id, text=response.content
            )
        self._finished(
            request,
            finish_reason=response.finish_reason,
            text_chars=len(response.content),
            tool_call_count=len(response.tool_calls),
            elapsed_ms=(self._timer() - started) * 1000.0,
            usage=response.usage,
        )
        _log.debug("model.response.text", text=response.content)
        return ModelOutcome(text=response.content, tool_calls=response.tool_calls)

    # ---- 流式 ----

    def _stream(self, request: ModelRequest) -> ModelOutcome | LoopStop:
        started = self._timer()
        try:
            chunks = self._gateway.stream(request)
        except ModelBadRequestError:
            # provider 不具备流式能力 (gateway 在产出首块前即拒绝): 回退非流式.
            return self._complete(request)
        accumulator = StreamAccumulator()
        tail: ModelStreamChunk | None = None
        for chunk in chunks:
            accumulator.add(chunk)
            if chunk.delta_text:
                self._events.model_delta(
                    request_id=request.request_id, text=chunk.delta_text
                )
            tail = chunk
        elapsed_ms = (self._timer() - started) * 1000.0
        self._partial_answer = accumulator.text or None
        self._record_stream_draft(request, tail, accumulator, elapsed_ms)

        if tail is not None and tail.interrupted:
            return self._interrupted(request, tail)
        if accumulator.has_partial_tool_calls():
            # 半截的 tool call delta: 流没断但参数不完整, 补全它等于替模型编参数.
            #
            # 这次模型调用确实失败了, 所以照常收尾; 但本轮不一定要结束, 所以抛而不是
            # return —— 由循环的统一处理决定重试还是中止, 与非流式路径走同一条判断.
            self._failed(
                request, "partial_tool_call", render_notice("stop.partial_tool_call")
            )
            raise MalformedToolCallError(render_notice("stop.partial_tool_call"))

        tool_calls = accumulator.tool_calls()
        self._finished(
            request,
            finish_reason=accumulator.finish_reason or FinishReason.STOP,
            text_chars=len(accumulator.text),
            tool_call_count=len(tool_calls),
            elapsed_ms=elapsed_ms,
            usage=accumulator.usage,
        )
        _log.debug("model.response.text", text=accumulator.text)
        return ModelOutcome(text=accumulator.text, tool_calls=tool_calls)

    def _interrupted(self, request: ModelRequest, tail: ModelStreamChunk) -> LoopStop:
        """中断也要收尾这次调用。

        不收尾的话终端的活动区会停在"正在思考"上等一个永远不来的结束事件 (ADR-0016 §5)。
        """
        if tail.finish_reason is FinishReason.USER_CANCELLED:
            notice = render_notice("stop.cancelled")
            self._failed(request, "user_cancelled", notice, retryable=True)
            return LoopStop(LoopStopReason.USER_CANCELLED, message=notice)
        notice = render_notice("stop.stream_interrupted")
        self._failed(request, "stream_interrupted", notice)
        return LoopStop(LoopStopReason.MODEL_ERROR_BLOCKING, message=notice)

    def _record_stream_draft(
        self,
        request: ModelRequest,
        tail: ModelStreamChunk | None,
        accumulator: StreamAccumulator,
        elapsed_ms: float,
    ) -> None:
        """由流式收尾块合成 ModelResponse 复用计量; 取消/中断也记录估算用量 (§9)."""
        if tail is None or accumulator.usage is None:
            return
        self._on_usage(
            self._meter.build_draft(
                request,
                ModelResponse(
                    request_id=request.request_id,
                    provider=tail.provider,
                    model=tail.model,
                    content=accumulator.text,
                    finish_reason=accumulator.finish_reason or FinishReason.STOP,
                    usage=accumulator.usage,
                    latency_ms=elapsed_ms,
                ),
            )
        )

    # ---- 两条路共用的收尾 ----

    def _finished(
        self,
        request: ModelRequest,
        *,
        finish_reason: FinishReason,
        text_chars: int,
        tool_call_count: int,
        elapsed_ms: float,
        usage: ModelUsage | None,
    ) -> None:
        _log.info(
            "model.response",
            finish_reason=finish_reason.value,
            text_chars=text_chars,
            tool_calls=tool_call_count,
            elapsed_ms=elapsed_ms,
            input_tokens=None if usage is None else usage.input_tokens,
            output_tokens=None if usage is None else usage.output_tokens,
            cached_tokens=None if usage is None else usage.cached_input_tokens,
            reasoning_tokens=None if usage is None else usage.reasoning_tokens,
            estimated=None if usage is None else usage.estimated,
        )
        self._events.model_completed(
            request_id=request.request_id,
            origin=request.origin.value,
            finish_reason=finish_reason,
            text_chars=text_chars,
            tool_call_count=tool_call_count,
            elapsed_ms=elapsed_ms,
            usage=usage,
        )

    def _failed(
        self,
        request: ModelRequest,
        error_kind: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        _log.error(
            "model.failed", error_kind=error_kind, message=message, retryable=retryable
        )
        self._events.model_failed(
            request_id=request.request_id,
            error_kind=error_kind,
            message=message,
            retryable=retryable,
        )
