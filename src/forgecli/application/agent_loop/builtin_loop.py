"""BuiltinAgentLoop：MVP 唯一的 ReAct 循环实现（ADR-0010 §3 / §5 / §13-3）。

2026-07-24 切片只走一步：一次模型调用 -> AnswerAction -> observe 反馈后
LoopStop(FINAL_ANSWER)。所有模型调用经统一 LlmGateway；本实现只产出意图，
不执行任何副作用——事件与 usage 落盘由 AgentTurnService 完成（§2）。

usage 草稿经只读属性 usage_drafts 交回驱动方；流式增量经注入的 on_delta 回调
外送（装配方给渲染器的 bound method，循环不感知渲染细节）。取消依赖
ModelRequest.cancel_token 的协作检查，中断的流按 §9 收尾块归一为 LoopStop。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from types import MappingProxyType

from forgecli.application.agent_loop.events import LoopEventBus
from forgecli.application.agent_loop.loop import AgentLoop
from forgecli.application.llm.error_hints import actionable_message
from forgecli.application.llm.gateway.errors import (
    ModelBadRequestError,
    ModelCancelledError,
    ModelGatewayError,
)
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.streaming import StreamAccumulator
from forgecli.application.llm.metering import UsageMeter
from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopDecision,
    LoopObservation,
    LoopStepResult,
    LoopStop,
)
from forgecli.domain.agent.events import LoopEvent, LoopEventKind
from forgecli.domain.agent.state import LoopInput
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams
from forgecli.domain.model.request import ModelRequest
from forgecli.domain.model.response import (
    FinishReason,
    ModelResponse,
)
from forgecli.domain.model.selection import CurrentModelSelection
from forgecli.domain.model.streaming import ModelStreamChunk
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.shared.cancellation import CancelToken


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


class BuiltinAgentLoop(AgentLoop):
    """单步 ReAct 循环：每 turn 一个新实例（由装配方以工厂创建）。"""

    def __init__(
        self,
        gateway: LlmGateway,
        usage_meter: UsageMeter,
        *,
        request_id_factory: Callable[[], str] = _new_request_id,
        cancel_token_factory: Callable[[], CancelToken | None] = lambda: None,
        on_delta: Callable[[str], None] | None = None,
        event_bus: LoopEventBus | None = None,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gateway = gateway
        self._meter = usage_meter
        self._new_request_id = request_id_factory
        self._new_cancel_token = cancel_token_factory
        # on_delta 为 None 表示无渲染消费方：走非流式 complete（测试默认形态）。
        self._on_delta = on_delta
        self._bus = event_bus
        self._timer = timer
        self._started = False
        self._answered = False
        self._turn_id: str | None = None
        self._usage_drafts: list[UsageRecordDraft] = []
        self._partial_answer: str | None = None

    # ---- AgentLoop 端口 ----

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        if self._started:
            raise RuntimeError("BuiltinAgentLoop 每轮只能 start 一次（每 turn 新实例）")
        self._started = True
        self._turn_id = loop_input.turn_id
        self._publish(LoopEventKind.LOOP_STARTED, {"mode": loop_input.mode.value})

        request = self._build_request(loop_input)
        self._publish(LoopEventKind.MODEL_REQUESTED, {"request_id": request.request_id})
        try:
            result = self._call_model(request)
        except ModelCancelledError as exc:
            return self._stop(LoopStopReason.USER_CANCELLED, actionable_message(exc))
        except ModelGatewayError as exc:
            return self._stop(
                LoopStopReason.MODEL_ERROR_BLOCKING, actionable_message(exc)
            )
        if isinstance(result, LoopStop):
            self._publish(LoopEventKind.LOOP_STOPPED, {"reason": result.reason.value})
            return result
        self._answered = True
        self._publish(
            LoopEventKind.ACTION_REQUESTED, {"action": "answer", "chars": len(result)}
        )
        return LoopDecision(
            reason_summary="单步模型调用已产出最终回答",
            next_action=AnswerAction(text=result),
        )

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        if not self._started:
            raise RuntimeError("BuiltinAgentLoop 未 start，不接受 observe")
        if not self._answered:
            raise RuntimeError("BuiltinAgentLoop 尚未产出回答，不接受 observe")
        return self._stop(LoopStopReason.FINAL_ANSWER, None)

    # ---- 驱动方读取（不在冻结 ABC 上，经 getattr 鸭子类型消费）----

    @property
    def usage_drafts(self) -> tuple[UsageRecordDraft, ...]:
        """本轮产生的 usage 草稿（落盘由 AgentTurnService 执行）。"""
        return tuple(self._usage_drafts)

    @property
    def partial_answer(self) -> str | None:
        """流式中断时已累积的部分回答文本（无则为 None）。"""
        return self._partial_answer

    # ---- 内部 ----

    def _build_request(self, loop_input: LoopInput) -> ModelRequest:
        return ModelRequest(
            request_id=self._new_request_id(),
            session_id=loop_input.session_id,
            turn_id=loop_input.turn_id,
            origin=RequestOrigin.CHAT,
            model_selection=CurrentModelSelection(),
            # 当前用户消息由 AgentTurnService 装入 context_package，此处不重复追加。
            messages=loop_input.context_package.messages,
            params=ModelParams(),
            system_prompt=loop_input.context_package.system_prompt,
            cancel_token=self._new_cancel_token(),
            # metadata 只放脱敏 mode 摘要（ADR-0011 §3.3）；mode policy 不进请求。
            metadata=MappingProxyType({"mode": loop_input.mode.value}),
        )

    def _call_model(self, request: ModelRequest) -> str | LoopStop:
        if self._on_delta is None:
            return self._call_complete(request)
        return self._call_stream(request, self._on_delta)

    def _call_complete(self, request: ModelRequest) -> str:
        response = self._gateway.complete(request)
        self._usage_drafts.append(self._meter.build_draft(request, response))
        self._publish(
            LoopEventKind.MODEL_COMPLETED,
            {
                "request_id": request.request_id,
                "finish_reason": response.finish_reason.value,
                "chars": len(response.content),
            },
        )
        return response.content

    def _call_stream(
        self, request: ModelRequest, on_delta: Callable[[str], None]
    ) -> str | LoopStop:
        started = self._timer()
        try:
            chunks = self._gateway.stream(request)
        except ModelBadRequestError:
            # provider 不具备流式能力（gateway 在产出首块前即拒绝）：回退非流式。
            return self._call_complete(request)
        accumulator = StreamAccumulator()
        tail: ModelStreamChunk | None = None
        for chunk in chunks:
            accumulator.add(chunk)
            if chunk.delta_text:
                on_delta(chunk.delta_text)
            tail = chunk
        elapsed_ms = (self._timer() - started) * 1000.0
        self._partial_answer = accumulator.text or None
        self._record_stream_draft(request, tail, accumulator, elapsed_ms)
        if tail is not None and tail.interrupted:
            if tail.finish_reason is FinishReason.USER_CANCELLED:
                return LoopStop.of(
                    LoopStopReason.USER_CANCELLED, message="本轮回复已取消。"
                )
            return LoopStop.of(
                LoopStopReason.MODEL_ERROR_BLOCKING,
                message="流式响应中断，本轮回复失败。",
            )
        self._publish(
            LoopEventKind.MODEL_COMPLETED,
            {
                "request_id": request.request_id,
                "finish_reason": (accumulator.finish_reason or FinishReason.STOP).value,
                "chars": len(accumulator.text),
            },
        )
        return accumulator.text

    def _record_stream_draft(
        self,
        request: ModelRequest,
        tail: ModelStreamChunk | None,
        accumulator: StreamAccumulator,
        elapsed_ms: float,
    ) -> None:
        """由流式收尾块合成 ModelResponse 复用计量；取消/中断也记录估算用量（§9）。"""
        if tail is None or accumulator.usage is None:
            return
        response = ModelResponse(
            request_id=request.request_id,
            provider=tail.provider,
            model=tail.model,
            content=accumulator.text,
            finish_reason=accumulator.finish_reason or FinishReason.STOP,
            usage=accumulator.usage,
            latency_ms=elapsed_ms,
        )
        self._usage_drafts.append(self._meter.build_draft(request, response))

    def _stop(self, reason: LoopStopReason, message: str | None) -> LoopStop:
        self._publish(LoopEventKind.LOOP_STOPPED, {"reason": reason.value})
        return LoopStop.of(reason, message=message)

    def _publish(self, kind: LoopEventKind, payload: dict[str, object]) -> None:
        if self._bus is None or self._turn_id is None:
            return
        self._bus.publish(
            LoopEvent(
                kind=kind,
                turn_id=self._turn_id,
                payload=MappingProxyType(payload),
            )
        )
