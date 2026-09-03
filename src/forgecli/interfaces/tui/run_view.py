"""把一轮的 ``AgentRunEvent`` 画成终端时间线 (ADR-0016 §5, ADR-0045).

两件事必须分开: 事件由 Agent 线程产生, 但**只有主线程能往终端写**. 两个线程同时
print 会把流式正文和审批卡片搅在一起, 而那正是用户要读着做决定的东西. 所以这里是
一个只管收的订阅者, 加一个只在主线程调用的画笔.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from forgecli.application.agent_run.events import AgentRunEventSubscriber
from forgecli.domain.agent.run_events import (
    AgentRunEvent,
    AgentRunEventKind,
    ContextCompactedPayload,
    DecisionSummaryPayload,
    ModelFailedPayload,
    ModelUsagePayload,
    PlanProposedPayload,
    PolicyResolvedPayload,
    ReasoningStatus,
    ReasoningStatusPayload,
    TextDeltaPayload,
    TodoUpdatedPayload,
    ToolCompletedPayload,
    ToolPreparedPayload,
    ToolQueuedPayload,
    ToolStartedPayload,
    TurnFinishedPayload,
)
from forgecli.interfaces.tui.console import (
    DOT,
    STYLE_ACCENT,
    STYLE_DIM,
    STYLE_ERROR,
    STYLE_OK,
    STYLE_WARN,
    SUB,
    THINK,
    truncate,
)

# 一行摘要的宽度上限. 一条 shell 命令可能有几千字符, 原样打出来会把时间线冲掉;
# 完整正文在审批卡片里逐字给, 那里才是用来做决定的地方.
_SUMMARY_WIDTH = 96
_ARGS_WIDTH = 72
# 终态那一行的失败说明. 比工具摘要给得多: 一轮为什么没跑成, 用户唯一能读到的就是它.
_DETAIL_WIDTH = 160


class RunEventCollector(AgentRunEventSubscriber):
    """Agent 线程投递, 主线程 ``drain``. 不做任何渲染."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: deque[AgentRunEvent] = deque()

    def on_event(self, event: AgentRunEvent) -> None:
        with self._lock:
            self._items.append(event)

    def drain(self) -> tuple[AgentRunEvent, ...]:
        with self._lock:
            items = tuple(self._items)
            self._items.clear()
        return items


class TurnMetrics:
    """一轮的读数. 累加在这里而不是画笔里: 终态那一行要在事件流结束之后才打。"""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.reasoning_tokens = 0
        self.total_tokens = 0
        self.estimated = False

    def add(self, usage: ModelUsagePayload) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.reasoning_tokens += usage.reasoning_tokens
        self.total_tokens += usage.total_tokens
        self.estimated = self.estimated or usage.estimated


class TerminalRunView:
    """一轮一个. 只在主线程调用."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.metrics = TurnMetrics()
        self.finished = False
        self._streaming = False
        self._thinking = False
        # DECISION_SUMMARY 解释的是紧跟着的那一步, 不单独占一行 (ADR-0025 决策 32
        # 修订): 让它自成一行, 连着六次读文件就没有一次是相邻的.
        self._pending_reason = ""
        self._arguments: dict[str, tuple[tuple[str, str], ...]] = {}
        self._policies: dict[str, PolicyResolvedPayload] = {}

    # ---- 主入口 ----

    def handle(self, event: AgentRunEvent) -> None:
        kind = event.kind
        payload = event.payload
        if kind is AgentRunEventKind.MODEL_OUTPUT_DELTA and isinstance(
            payload, TextDeltaPayload
        ):
            self._stream(payload.text)
        elif kind is AgentRunEventKind.MODEL_REASONING_STATUS and isinstance(
            payload, ReasoningStatusPayload
        ):
            self._reasoning(payload)
        elif kind is AgentRunEventKind.MODEL_COMPLETED:
            self._close_stream()
        elif kind is AgentRunEventKind.MODEL_USAGE and isinstance(
            payload, ModelUsagePayload
        ):
            self._usage(payload)
        elif kind is AgentRunEventKind.MODEL_FAILED and isinstance(
            payload, ModelFailedPayload
        ):
            self._line(
                f"模型调用失败: {payload.error_kind} {payload.message}", STYLE_ERROR
            )
        elif kind is AgentRunEventKind.CONTEXT_COMPACTED and isinstance(
            payload, ContextCompactedPayload
        ):
            self._compacted(payload)
        elif kind is AgentRunEventKind.DECISION_SUMMARY and isinstance(
            payload, DecisionSummaryPayload
        ):
            self._pending_reason = payload.reason_summary
        elif (
            kind is AgentRunEventKind.TOOL_QUEUED
            and isinstance(payload, ToolQueuedPayload)
            or kind is AgentRunEventKind.TOOL_PREPARED
            and isinstance(payload, ToolPreparedPayload)
        ):
            self._remember(event.tool_call_id, payload.arguments)
        elif kind is AgentRunEventKind.POLICY_RESOLVED and isinstance(
            payload, PolicyResolvedPayload
        ):
            self._policy(event.tool_call_id, payload)
        elif kind is AgentRunEventKind.TOOL_STARTED and isinstance(
            payload, ToolStartedPayload
        ):
            self._tool_started(event.tool_call_id, payload)
        elif kind in (
            AgentRunEventKind.TOOL_COMPLETED,
            AgentRunEventKind.TOOL_CANCELLED,
        ) and isinstance(payload, ToolCompletedPayload):
            self._tool_finished(event.tool_call_id, payload)
        elif kind is AgentRunEventKind.PLAN_PROPOSED and isinstance(
            payload, PlanProposedPayload
        ):
            self._entry(
                f"计划 {payload.title}",
                f"r{payload.revision} · {payload.step_count} 步",
            )
        elif kind is AgentRunEventKind.TODO_UPDATED and isinstance(
            payload, TodoUpdatedPayload
        ):
            self._todo(payload)
        elif kind in (
            AgentRunEventKind.TURN_COMPLETED,
            AgentRunEventKind.TURN_CANCELLED,
            AgentRunEventKind.TURN_FAILED,
        ) and isinstance(payload, TurnFinishedPayload):
            self._turn_finished(kind, payload)

    # ---- 流式正文 ----

    def _stream(self, text: str) -> None:
        """逐段打模型正文.

        ``soft_wrap`` 是必须的: Rich 默认按自己算出的宽度断行, 而两次 print 之间它
        并不知道上一段停在第几列, 于是每一段增量都会从头当作新的一段来排.
        """
        if not text:
            return
        if self._thinking:
            self._thinking = False
        if not self._streaming:
            self.console.print()
            self._streaming = True
        self.console.print(text, end="", markup=False, soft_wrap=True)

    def _close_stream(self) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False

    def _reasoning(self, payload: ReasoningStatusPayload) -> None:
        # 只报状态, 不报内容 (ADR-0016 §6): 供应商开了 thinking 却不返回可展示摘要是
        # 常态, 终端能说的就是"在想".
        if payload.status is ReasoningStatus.STARTED and not self._thinking:
            self._close_stream()
            self.console.print(Text(f"{THINK} 思考中…", style=STYLE_DIM))
            self._thinking = True

    # ---- 结构化行 ----

    def _entry(self, title: str, detail: str = "", style: str = "") -> None:
        """时间线上的一步. 与 Web 的轨道圆点同一个位置."""
        self._close_stream()
        line = Text(f"{DOT} ", style=style or STYLE_ACCENT)
        line.append(title, style=style or "bold")
        if detail:
            line.append(f"  {detail}", style=STYLE_DIM)
        self.console.print(line)
        if self._pending_reason:
            self._sub(self._pending_reason, STYLE_DIM)
            self._pending_reason = ""

    def _sub(self, text: str, style: str = STYLE_DIM) -> None:
        self.console.print(
            Text(f"  {SUB} {truncate(text, _SUMMARY_WIDTH)}", style=style)
        )

    def _line(self, text: str, style: str) -> None:
        self._close_stream()
        self.console.print(Text(f"{DOT} {text}", style=style))

    # ---- 各类事件 ----

    def _usage(self, payload: ModelUsagePayload) -> None:
        self.metrics.add(payload)
        # 压缩烧掉的那一笔单独标出来 (ADR-0037): 它不发 MODEL_STARTED, 用户拿"模型 N 次"
        # 去核这笔账时会发现一次对不上的调用.
        if payload.origin == "compact":
            self._sub(f"压缩用掉 {payload.total_tokens} tokens")

    def _compacted(self, payload: ContextCompactedPayload) -> None:
        self._entry(
            "上下文压缩",
            f"省下 {payload.tokens_saved} tokens · 折叠 {payload.messages_replaced} 条",
        )

    def _remember(
        self, tool_call_id: str | None, arguments: Sequence[tuple[str, str]]
    ) -> None:
        if tool_call_id and arguments:
            self._arguments[tool_call_id] = tuple(arguments)

    def _policy(self, tool_call_id: str | None, payload: PolicyResolvedPayload) -> None:
        if tool_call_id:
            self._policies[tool_call_id] = payload
        # 自动放行不打断时间线; 要人点头或直接拒了的那两种必须看得见.
        if payload.decision in ("allow", "allow_once"):
            return
        style = STYLE_ERROR if payload.decision == "deny" else STYLE_WARN
        detail = payload.detail or payload.reason
        self._entry(
            f"裁决 {payload.tool_name}", f"{payload.decision} · {detail}", style
        )
        if payload.risk_facts:
            self._sub("风险: " + ", ".join(payload.risk_facts))

    def _tool_started(
        self, tool_call_id: str | None, payload: ToolStartedPayload
    ) -> None:
        arguments = self._arguments.get(tool_call_id or "", ())
        self._entry(payload.tool_name, _format_arguments(arguments))

    def _tool_finished(
        self, tool_call_id: str | None, payload: ToolCompletedPayload
    ) -> None:
        if payload.tool_name and not payload.executed:
            # 连执行都没发生的调用没有 TOOL_STARTED, 时间线上必须自己补一行, 否则
            # 一次被拒或没等到批准的调用在终端上什么都不会留下.
            arguments = self._arguments.get(tool_call_id or "", ())
            self._entry(payload.tool_name, _format_arguments(arguments), STYLE_WARN)
        summary = payload.result_summary or payload.error_summary or payload.status
        style = STYLE_OK if payload.status == "ok" else STYLE_ERROR
        if payload.side_effect_unknown:
            # "没成功"与"没发生"是两回事 (ADR-0016 §10.1): 执行中被打断且不幂等时,
            # 显示成普通失败就是在替一次可能已经发生的写入下结论.
            style = STYLE_WARN
            summary = f"{summary} · 已执行部分未知"
        self._sub(summary, style)

    def _todo(self, payload: TodoUpdatedPayload) -> None:
        detail = f"{payload.done}/{payload.total}"
        if payload.current:
            detail += f" · 当前: {truncate(payload.current, 48)}"
        self._entry("待办", detail)

    def _turn_finished(
        self, kind: AgentRunEventKind, payload: TurnFinishedPayload
    ) -> None:
        self._close_stream()
        self.finished = True
        seconds = payload.elapsed_ms / 1000
        chips = [
            f"{seconds:.1f}s",
            f"模型 {payload.model_calls} 次",
            f"工具 {payload.tool_calls} 次",
            f"{self.metrics.total_tokens} tokens"
            + ("(估算)" if self.metrics.estimated else ""),
        ]
        style = {
            AgentRunEventKind.TURN_COMPLETED: STYLE_DIM,
            AgentRunEventKind.TURN_CANCELLED: STYLE_WARN,
            AgentRunEventKind.TURN_FAILED: STYLE_ERROR,
        }[kind]
        label = {
            AgentRunEventKind.TURN_COMPLETED: "",
            AgentRunEventKind.TURN_CANCELLED: "已停止 · ",
            AgentRunEventKind.TURN_FAILED: "本轮失败 · ",
        }[kind]
        detail = f"  {label}" + " · ".join(chips)
        if payload.detail:
            detail += f" · {truncate(payload.detail, _DETAIL_WIDTH)}"
        self.console.print(Text(detail, style=style))


def _format_arguments(arguments: Sequence[tuple[str, str]]) -> str:
    """入参压成一行. 逐字展示留给审批卡片, 这里只回答"它在干什么"."""
    if not arguments:
        return ""
    parts = [f"{name}={truncate(value, 40)}" for name, value in arguments]
    return truncate(" ".join(parts), _ARGS_WIDTH)
