"""把循环内部发生的事翻译成 AgentRunEvent (ADR-0016).

分出来是因为它与控制流回答的是两个问题: 循环决定"下一步做什么", 这里只决定"这件事
在屏幕上长什么样". 一百多行的 payload 拼装夹在控制流中间, 读循环的人要反复跳过它们。

**分工不在这里**: 循环拥有 turn 生命周期, 步骤切换, 模型调用和"模型请求了哪个工具";
工具真正被裁决与执行之后的事件由 ToolRequestCoordinator 发布 —— 循环看不见裁决与执行,
编不出那些事实 (ADR-0016 §4.3). 这个类不会因为拿到了总线就多发几条。

步骤序号也归它: 它只有两个消费方, 事件信封与日志上下文, 两个都是可观测性。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_run.scrubbing import scrub_arguments
from forgecli.application.context.window_manager import WindowFitResult
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    ContextCompactedPayload,
    DecisionSummaryPayload,
    ModelCompletedPayload,
    ModelFailedPayload,
    ModelStartedPayload,
    ModelUsagePayload,
    ReasoningStatus,
    ReasoningStatusPayload,
    RunEventPayload,
    TextDeltaPayload,
    ToolCompletedPayload,
    ToolQueuedPayload,
    TurnFinishedPayload,
    WorkspaceChangedPayload,
)
from forgecli.domain.agent.stop import LoopStopReason, StopClassification
from forgecli.domain.model.response import FinishReason, ModelUsage
from forgecli.domain.tool.tool_call import ToolCall
from forgecli.domain.workspace.changes import WorkspaceChange

__all__ = ["LoopEventPublisher"]


class LoopEventPublisher:
    """一轮的事件出口。没有总线时全部方法都是空操作 —— 展示缺席不影响状态推进。"""

    def __init__(
        self,
        bus: AgentRunEventBus | None,
        *,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bus = bus
        self._timer = timer
        self._session_id = ""
        self._turn_id: str | None = None
        self._step = 0
        self._started_at = 0.0

    # ---- 轮次身份与步骤 ----

    def start_turn(self, *, session_id: str, turn_id: str) -> None:
        self._session_id = session_id
        self._turn_id = turn_id
        self._started_at = self._timer()

    @property
    def step(self) -> int:
        return self._step

    def next_step(self) -> int:
        """进入下一步 (调模型 / 派工具 / 出回答各算一步), 返回新的步号。"""
        self._step += 1
        return self._step

    def reset_step(self) -> None:
        self._step = 0

    def elapsed_ms(self) -> float:
        return (self._timer() - self._started_at) * 1000.0

    # ---- 模型调用 ----

    def model_started(self, *, request_id: str, call_index: int) -> None:
        self._publish(
            AgentRunEventKind.MODEL_STARTED,
            ModelStartedPayload(call_index=call_index),
            request_id=request_id,
        )
        # thinking 是否有可展示内容由供应商决定; 这里只如实报"开始想了".
        # 不可用时终端显示状态即可, 绝不从回答或 token 数倒推思维链 (ADR-0016 §6).
        self._publish(
            AgentRunEventKind.MODEL_REASONING_STATUS,
            ReasoningStatusPayload(status=ReasoningStatus.STARTED),
            request_id=request_id,
        )

    def model_delta(self, *, request_id: str, text: str) -> None:
        self._publish(
            AgentRunEventKind.MODEL_OUTPUT_DELTA,
            TextDeltaPayload(text=text),
            request_id=request_id,
        )

    def model_completed(
        self,
        *,
        request_id: str,
        origin: str,
        finish_reason: FinishReason,
        text_chars: int,
        tool_call_count: int,
        elapsed_ms: float,
        usage: ModelUsage | None,
    ) -> None:
        self._publish(
            AgentRunEventKind.MODEL_REASONING_STATUS,
            ReasoningStatusPayload(status=ReasoningStatus.COMPLETED),
            request_id=request_id,
        )
        self._publish(
            AgentRunEventKind.MODEL_COMPLETED,
            ModelCompletedPayload(
                finish_reason=finish_reason.value,
                text_chars=text_chars,
                tool_call_count=tool_call_count,
                elapsed_ms=elapsed_ms,
            ),
            request_id=request_id,
        )
        if usage is not None:
            self._publish(
                AgentRunEventKind.MODEL_USAGE,
                ModelUsagePayload(
                    origin=origin,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    reasoning_tokens=usage.reasoning_tokens or 0,
                    cached_tokens=usage.cached_input_tokens or 0,
                    total_tokens=usage.total_tokens or 0,
                    estimated=usage.estimated,
                ),
                request_id=request_id,
            )

    def model_failed(
        self, *, request_id: str, error_kind: str, message: str, retryable: bool = False
    ) -> None:
        self._publish(
            AgentRunEventKind.MODEL_FAILED,
            ModelFailedPayload(
                error_kind=error_kind, message=message, retryable=retryable
            ),
            request_id=request_id,
        )

    # ---- 工具 ----

    def tool_batch(self, calls: tuple[ToolCall, ...]) -> None:
        """在首个调用派发前发布模型请求的完整批次。"""
        total = len(calls)
        for index, call in enumerate(calls):
            self._publish(
                AgentRunEventKind.TOOL_QUEUED,
                ToolQueuedPayload(
                    tool_name=call.name,
                    # 这是该调用之后还有几个同批调用；最后一条恒为 0，前端据此把整批
                    # 事件一次刷新出来，而不是为 N 个调用渲染 N 次。
                    queue_position=total - index - 1,
                    # 连 prepare 都走不到的调用 (工具名不存在, schema 不合法) 只有排队与
                    # 终态两条事件. 入参不在这里发出去, 它在整条时间线上一次都不会出现.
                    arguments=scrub_arguments(call.arguments),
                ),
                tool_call_id=call.tool_call_id,
            )

    def tool_rejected(self, call: ToolCall, *, notice: str, code: str) -> None:
        """循环自己挡下的调用 (重复调用)。整批请求已经提前可见，它必须有未执行终态。"""
        self._tool_not_run(
            call, AgentRunEventKind.TOOL_COMPLETED, "rejected", notice, code
        )

    def tool_abandoned(self, call: ToolCall, *, notice: str) -> None:
        """排队中就被放弃的调用 (本轮提前收摊)。同样要有未执行终态。"""
        self._tool_not_run(
            call, AgentRunEventKind.TOOL_CANCELLED, "not_run", notice, "abandoned"
        )

    def _tool_not_run(
        self,
        call: ToolCall,
        kind: AgentRunEventKind,
        status: str,
        notice: str,
        code: str,
    ) -> None:
        """循环仅有的两种工具终态事件: 它们都没进过协调器。

        真正执行过的调用一律由协调器收尾 —— 循环拿到的只是一段回填文本, 用它冒充执行
        结论会让终端显示的"完成"与真正发生的事脱节 (ADR-0016 §4.3)。
        """
        self._publish(
            kind,
            ToolCompletedPayload(
                tool_name=call.name,
                status=status,
                error_summary=notice,
                error_code=code,
                executed=False,
            ),
            tool_call_id=call.tool_call_id,
        )

    # ---- 循环自己的判断 ----

    def decision(self, reason_summary: str) -> None:
        """行动摘要。

        DECISION_SUMMARY 是 ForgeCLI 自己能解释的行动摘要, 与供应商 reasoning 分开命名:
        混成一栏, 用户会以为看到的是模型在想什么 (ADR-0016 §6).

        空摘要不发事件: 大多数派发没有需要解释的理由, 而发一条内容等于"我要调这个工具"
        的摘要, 只会把真正的摘要 (连续无新信息, 格式损坏重试) 挤成同一种东西.
        """
        if not reason_summary:
            return
        self._publish(
            AgentRunEventKind.DECISION_SUMMARY,
            DecisionSummaryPayload(reason_summary=reason_summary),
        )

    def compaction(self, result: WindowFitResult) -> None:
        """把压缩这件事报出去 (ADR-0037).

        以前它在界面上完全不可见: 用户看到的是"卡了几秒", 而实际发生的是一次额外的
        模型调用把一段历史换掉了. 两类事件都发 —— 淘汰本身 (省下多少) 与它的用量
        (花掉多少) 是方向相反的两个数, 只报一个都会读错.
        """
        for draft in result.drafts:
            self._publish(
                AgentRunEventKind.CONTEXT_COMPACTED,
                ContextCompactedPayload(
                    level=draft.level.value,
                    tokens_before=draft.tokens_before,
                    tokens_after=draft.tokens_after,
                    tokens_saved=draft.tokens_saved,
                    messages_replaced=draft.messages_replaced,
                    provider=draft.provider,
                    model=draft.model,
                ),
            )
        for usage in result.usage_drafts:
            self._publish(
                AgentRunEventKind.MODEL_USAGE,
                ModelUsagePayload.from_draft(usage),
                request_id=usage.request_id,
            )

    def workspace_changed(self, changes: tuple[WorkspaceChange, ...]) -> None:
        self._publish(
            AgentRunEventKind.WORKSPACE_CHANGED,
            WorkspaceChangedPayload(changes=changes),
        )

    def turn_finished(
        self, reason: LoopStopReason, *, detail: str, model_calls: int, tool_calls: int
    ) -> None:
        # 只有 BLOCKING 算失败. 可恢复暂停 (等审批 / 等输入) 也是"这一轮结束了",
        # 页面要收掉活动区; 它与失败的区别由 TurnFinishedPayload.status 表达.
        kind = (
            AgentRunEventKind.TURN_FAILED
            if reason.classification is StopClassification.BLOCKING
            else AgentRunEventKind.TURN_COMPLETED
        )
        if reason is LoopStopReason.USER_CANCELLED:
            # USER_CANCELLED 归类是 BLOCKING, 但对用户来说它是"我按了 Ctrl-C", 不是故障.
            kind = AgentRunEventKind.TURN_CANCELLED
        self._publish(
            kind,
            TurnFinishedPayload(
                status=reason.value,
                elapsed_ms=self.elapsed_ms(),
                model_calls=model_calls,
                tool_calls=tool_calls,
                detail=detail,
            ),
        )

    # ---- 唯一的出口 ----

    def _publish(
        self,
        kind: AgentRunEventKind,
        payload: RunEventPayload,
        *,
        request_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        if self._bus is None or self._turn_id is None:
            return
        self._bus.publish(
            kind,
            session_id=self._session_id,
            turn_id=self._turn_id,
            payload=payload,
            step_index=self._step,
            request_id=request_id,
            tool_call_id=tool_call_id,
        )
