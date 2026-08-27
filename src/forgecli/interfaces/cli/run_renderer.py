"""TerminalRunRenderer: 一轮 turn 内唯一的终端输出协调者 (ADR-0016 §8).

终端原生模型 (append + scrollback), 不是全屏 TUI:

- **活动区**只有一行, 放当前还没完成的状态 ("正在思考", "正在执行 shell_run"). 它是
  transient 的, 退出时擦掉.
- **已完成的内容**用 console.print 提交到活动区上方, 进入原生 scrollback. 因此回答再长
  也不会被 Live 的高度裁掉, 也不产生"擦除 + 重印"式的滚动历史重复.

单一写入者不是风格问题: Live 与普通 print 抢同一块屏幕区域时, 输出会互相覆盖或错位.
审批要读输入, 所以 APPROVAL_REQUESTED 时先收掉活动区, 把终端交给 ApprovalService,
APPROVAL_RESOLVED 之后再恢复.

**正文按 request_id 分块** (§5). 一轮里模型可能被调用多次: 说一句 -> 调工具 -> 再说一句.
每次调用的正文是独立的一块, 各自带 "●" 标记, 而不是一整轮拼成一段. 块的边界就是时间线
的边界 —— 一块正文必须在它后面那条过程行**之前**全部提交完, 否则模型在调工具之前说的话
会显示到工具结果的下面去, 读起来像是它执行完才说的.

工具入参与裁决理由逐字展示, 不做脱敏, 也没有展示密度开关: 少显示什么都得有人来定,
而"定不下来"最后总是变成默认少显示.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.text import Text

from forgecli.application.agent_run.events import AgentRunEventSubscriber
from forgecli.domain.agent.run_events import (
    AgentRunEvent,
    AgentRunEventKind,
    ApprovalRequestedPayload,
    ContextCompactedPayload,
    ModelFailedPayload,
    ModelUsagePayload,
    PolicyResolvedPayload,
    ReasoningStatus,
    ReasoningStatusPayload,
    RunPhase,
    StepStartedPayload,
    TextDeltaPayload,
    ToolCompletedPayload,
    ToolPreparedPayload,
    ToolQueuedPayload,
    TurnFinishedPayload,
)
from forgecli.domain.context.compaction import CompactionLevel
from forgecli.domain.model.origin import RequestOrigin
from forgecli.interfaces.cli.transcript import assistant_line

__all__ = ["TerminalRunRenderer"]

K = AgentRunEventKind

_PLACEHOLDER = "● 处理中..."
_STATUS_STYLE = "dim #94e2d5"
_PROCESS_STYLE = "dim"
# 单条摘要行的硬上限. 与密度无关: 一条几十 KB 的路径会把整屏冲掉.
_LINE_LIMIT = 400


def _clean(text: str, *, limit: int = _LINE_LIMIT) -> str:
    """剥控制字符 + 限长 + 转义 Rich markup.

    剥控制字符不是隐藏内容, 是防止参数与路径里的 ANSI/OSC 序列重画屏幕; Rich markup
    转义则保证显示的字符串与真正执行的一致.

    换行**转义成可见的两个字符**而不是删掉: 活动区是一行一条, 直接留着换行会把布局
    撑开, 而删掉会让多行内容拼成一行看不出接缝 —— 用户会以为那真的是一行.
    """
    stripped = "".join(
        char for char in text.replace("\n", "\\n") if char == "\t" or char >= " "
    ).replace("\x7f", "")
    if len(stripped) > limit:
        stripped = f"{stripped[:limit]}…"
    return escape(stripped)


# 用途标签取枚举值, 不在这里另写一个字符串 —— 那是事实不是措辞 (ADR-0031 收录判据).
_COMPACT_ORIGIN = RequestOrigin.COMPACT.value


def _tokens(value: int) -> str:
    """token 数的显示口径: 不足 1000 给整数, 到了 1000 换成 k.

    一位小数就够: 这个数字是拿来判断量级的 (这轮烧得多不多), 不是拿来对账的 ——
    要对账得看供应商账单, 而那里的口径本来就与本地估算不同.
    尾随的 .0 去掉: `1.0k` 看起来像是精确到百位, 其实不是.
    """
    if value < 1000:
        return str(value)
    return f"{value / 1000:.1f}".removesuffix(".0") + "k"


@dataclass
class _TurnUsage:
    """一轮里累计的 token 用量.

    含上下文压缩那次额外的模型调用 (ADR-0037): 它是这一轮真实烧掉的 token, 不算进来
    合计就对不上账单. 但**单独记一份** —— 压缩不是用户这句话直接引起的调用, 混进去
    不标出来, 用户会以为自己问一句话就烧了这么多.

    仍然不含安全分类器: 它走 gateway 的另一条路径, 不发运行事件, 有自己的预算, 也不
    属于"这轮对话"的内容 (ADR-0013 §11).
    """

    calls: int = 0
    total: int = 0
    compacted: int = 0
    estimated: bool = False

    def add(self, payload: ModelUsagePayload) -> None:
        self.calls += 1
        self.total += payload.total
        if payload.origin == _COMPACT_ORIGIN:
            self.compacted += payload.total
        # 一轮里只要有一次是估算的, 总数就是估算的: 混着报比全估算更容易误导.
        self.estimated = self.estimated or payload.estimated


class TerminalRunRenderer(AgentRunEventSubscriber):
    """订阅运行事件, 把一轮 turn 画成一条时间线."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._live: Live | None = None
        self._status = _PLACEHOLDER
        # **当前这一块**模型可见正文: 逐行提交, 半行留活动区. 换块时整个清空, 因此
        # _committed 始终是块内下标.
        self._buffer = ""
        self._committed = 0
        # 当前块属于哪次模型调用. None 有两种含义, 由 _block_open 区分: 块没开,
        # 或者事件没带 request_id (总线允许省略, 测试与非流式回退都会出现).
        self._request_id: str | None = None
        self._block_open = False
        # 本块还没提交过正文行 —— 决定 "●" 标记落在哪一行.
        self._block_first = True
        self._emitted_first = False
        self._suspended = False
        self._usage = _TurnUsage()

    # ---- 与 REPL 的接口 (与旧 StreamingTranscript 同形, 便于对调) ----

    @contextmanager
    def turn(self) -> Generator[None]:
        self._buffer = ""
        self._committed = 0
        self._request_id = None
        self._block_open = False
        self._block_first = True
        self._emitted_first = False
        self._status = _PLACEHOLDER
        self._suspended = False
        # 累计量按 turn 清零: "本轮消耗"要能对上用户刚提的这个问题.
        self._usage = _TurnUsage()
        live = Live(
            Text(_PLACEHOLDER, style=_STATUS_STYLE),
            console=self._console,
            transient=True,
            refresh_per_second=12,
        )
        self._live = live
        try:
            with live:
                yield
        finally:
            self._live = None

    def finish(self) -> None:
        """定稿: 把活动区剩余半行作为最后一行提交, 清空活动区."""
        if self._live is None:
            return
        self._close_block()
        self._live.update(Text(""))

    @property
    def had_output(self) -> bool:
        """本轮有没有打印过模型正文 (任意一块)."""
        return self._emitted_first

    @property
    def rendered_text(self) -> str:
        """**最后一块**已提交的正文.

        不是全轮拼接: 唯一的消费者是取消轮的收尾渲染, 它要从 `response.text` 里去掉
        "已经打过的那段". 而取消时 loop 交回的 partial_answer 只是最后一次模型调用累积
        的文本, 跨块拼接反而对不上.
        """
        return self._buffer[: self._committed]

    # ---- 事件入口 ----

    def on_event(self, event: AgentRunEvent) -> None:
        handler = _HANDLERS.get(event.kind)
        if handler is not None:
            handler(self, event)

    # ---- 各类事件 ----

    def _on_step(self, event: AgentRunEvent) -> None:
        payload = event.payload
        if not isinstance(payload, StepStartedPayload):
            return
        self._status = {
            RunPhase.THINKING: "● 正在思考...",
            RunPhase.TOOL: "● 正在准备工具...",
            RunPhase.ANSWER: "● 正在作答...",
        }[payload.phase]
        self._refresh()

    def _on_reasoning(self, event: AgentRunEvent) -> None:
        payload = event.payload
        if not isinstance(payload, ReasoningStatusPayload):
            return
        if payload.status is ReasoningStatus.STARTED:
            self._status = "● 正在思考..."
            self._refresh()

    def _on_output_delta(self, event: AgentRunEvent) -> None:
        payload = event.payload
        if not isinstance(payload, TextDeltaPayload):
            return
        self._open_block(event.request_id)
        self._buffer += payload.text
        self._flush_lines()
        self._refresh()

    def _on_model_completed(self, event: AgentRunEvent) -> None:
        """一次模型调用结束: 这一块正文到此为止, 半行立刻定稿.

        不能拖到 turn 收尾. 这次调用之后还会有用量行, 工具行和下一次调用的正文, 半行留在
        活动区就会被它们挤到时间线下游 —— 显示成模型"执行完工具才说的话". 这是唯一一个
        不打印任何行, 却必须收块的事件, 其余收块点都由 _process 兜住.
        """
        self._close_block()

    def _on_tool_queued(self, event: AgentRunEvent) -> None:
        payload = event.payload
        if not isinstance(payload, ToolQueuedPayload):
            return
        queued = (
            f" · 队列还有 {payload.queue_position}" if payload.queue_position else ""
        )
        self._status = f"● 正在执行 {payload.tool_name}"
        self._process(f"├─ 调用 {_clean(payload.tool_name)}{queued}")

    def _on_tool_prepared(self, event: AgentRunEvent) -> None:
        payload = event.payload
        if not isinstance(payload, ToolPreparedPayload):
            return
        # 入参逐字显示: 用户判断"要不要让这次调用发生"靠的就是它.
        line = " · ".join(f'{name}="{value}"' for name, value in payload.arguments)
        if payload.target_count:
            line = (
                f"{line} · {payload.target_count} 个目标"
                if line
                else (f"{payload.target_count} 个目标")
            )
        if line:
            self._process(f"│  {_clean(line)}")

    def _on_policy(self, event: AgentRunEvent) -> None:
        payload = event.payload
        if not isinstance(payload, PolicyResolvedPayload):
            return
        if payload.decision == "allow":
            # 允许是常态, 不值一行. 拒绝与需要确认必须显示.
            return
        self._process(f"│  安全裁决 {payload.decision} ({payload.reason})")

    def _on_approval_requested(self, event: AgentRunEvent) -> None:
        payload = event.payload
        # 交出终端: 审批要读输入, 活动区必须先收掉, 否则提示会被 Live 覆盖.
        self._suspend()
        if isinstance(payload, ApprovalRequestedPayload):
            kind = "逐次批准" if payload.mandatory else "确认"
            self._process(f"│  等待{kind}: {_clean(payload.tool_name)}")

    def _on_approval_resolved(self, event: AgentRunEvent) -> None:
        self._resume()

    def _on_tool_completed(self, event: AgentRunEvent) -> None:
        payload = event.payload
        if not isinstance(payload, ToolCompletedPayload):
            return
        bits = [payload.status]
        if payload.side_effect_unknown:
            # "没成功"与"没发生"是两回事, 不能显示成普通失败 (§10.1).
            bits.append("副作用未知")
        if payload.result_summary:
            bits.append(payload.result_summary)
        if payload.error_summary:
            bits.append(payload.error_summary)
        if payload.elapsed_ms:
            bits.append(f"{payload.elapsed_ms:.0f} ms")
        self._process(f"│  └─ {_clean(' · '.join(bits))}")
        self._status = _PLACEHOLDER
        self._refresh()

    def _on_model_usage(self, event: AgentRunEvent) -> None:
        """每次模型调用结束后报一次用量.

        报的是**本次调用**加上**本轮累计**两个数. 只给单次的话, 一轮里调了七次模型的
        长任务就得用户自己心算; 只给累计的话, 又看不出是哪一步吃掉的.

        第一次调用不重复打累计 —— 那时两个数必然相等.
        """
        payload = event.payload
        if not isinstance(payload, ModelUsagePayload):
            return
        self._usage.add(payload)
        bits = [f"输入 {_tokens(payload.input_tokens)}"]
        if payload.cached_tokens:
            # 缓存命中部分单价不同, 与总输入分开显示才看得出这次为什么便宜.
            bits.append(f"其中缓存 {_tokens(payload.cached_tokens)}")
        bits.append(f"输出 {_tokens(payload.output_tokens)}")
        if payload.reasoning_tokens:
            bits.append(f"思考 {_tokens(payload.reasoning_tokens)}")
        if self._usage.calls > 1:
            bits.append(f"本轮累计 {_tokens(self._usage.total)}")
        # 压缩那一笔标出来: 不标的话, 用户看到的是一次自己没发起过的调用在烧 token.
        label = "用量(压缩)" if payload.origin == _COMPACT_ORIGIN else "用量"
        line = f"│  {label} {' · '.join(bits)} tokens"
        if payload.estimated:
            # 供应商没回 usage, 这是本地估算. 不标出来用户会拿它去核账单.
            line = f"{line} (估算)"
        self._process(_clean(line))

    def _on_context_compacted(self, event: AgentRunEvent) -> None:
        """压缩这件事本身也要报 (ADR-0037).

        以前它完全不可见: 用户看到的是"卡了几秒", 而实际发生的是一次额外的模型调用把
        一段历史换掉了. 这一行只讲**省下**多少上下文; 花掉多少走用量行, 两个数方向
        相反, 并排出现才读得懂.
        """
        payload = event.payload
        if not isinstance(payload, ContextCompactedPayload):
            return
        summary = payload.level == CompactionLevel.SUMMARY.value
        detail = (
            f"顶替 {payload.messages_replaced} 条消息"
            if summary
            else f"改写 {payload.blocks_rewritten} 段工具输出"
        )
        kind = "摘要" if summary else "降级为引用"
        self._process(
            _clean(
                f"│  上下文压缩 ({kind}) "
                f"省下 {_tokens(payload.tokens_saved)} tokens · {detail}"
            )
        )

    def _on_model_failed(self, event: AgentRunEvent) -> None:
        payload = event.payload
        if not isinstance(payload, ModelFailedPayload):
            return
        self._process(
            f"│  模型调用失败 [{payload.error_kind}]: {_clean(payload.message)}"
        )

    def _on_turn_end(self, event: AgentRunEvent) -> None:
        payload = event.payload
        if not isinstance(payload, TurnFinishedPayload):
            return
        parts = [
            f"└─ {payload.status}",
            f"{payload.model_calls} 次模型调用",
            f"{payload.tool_calls} 次工具调用",
            f"{payload.elapsed_ms / 1000:.1f}s",
        ]
        if self._usage.calls:
            # 收尾行也带一次总量: 中途的用量行会被长回答顶到上面去.
            total = f"{_tokens(self._usage.total)} tokens"
            if self._usage.compacted:
                total = f"{total} (含压缩 {_tokens(self._usage.compacted)})"
            parts.append(f"{total} (含估算)" if self._usage.estimated else total)
        self._process(_clean(" · ".join(parts)))

    # ---- 终端写入 ----

    def _process(self, line: str) -> None:
        """提交一行过程信息到活动区上方.

        先收块: 有过程行要打, 就说明当前这块正文已经说完了. 把这条规则放在唯一的过程行
        出口上, 而不是散在每个事件处理里 —— 后者只要漏掉一个事件, 那条时间线就会错位,
        而这种错位在单元测试里往往看不出来 (输出都在, 只是顺序反了).
        """
        self._close_block()
        self._console.print(Text.from_markup(line, style=_PROCESS_STYLE))

    def _open_block(self, request_id: str | None) -> None:
        """确保有一块属于 request_id 的正文块可写.

        换 request 时先收掉上一块. 正常时序里 MODEL_COMPLETED 已经收过了, 这里是兜底:
        供应商或循环没发结束事件时, 至少两次调用的正文不会被拼成一段.
        """
        if self._block_open and request_id == self._request_id:
            return
        self._close_block()
        self._request_id = request_id
        self._block_open = True
        self._block_first = True
        self._buffer = ""
        self._committed = 0

    def _close_block(self) -> None:
        """把当前块剩余的半行提交, 关块. 幂等."""
        if not self._block_open:
            return
        remainder = self._buffer[self._committed :]
        if remainder:
            self._emit_answer(remainder)
            self._committed = len(self._buffer)
        self._block_open = False
        # 活动区里还挂着刚刚提交的那半行: 不刷掉, 同一段文字会同时出现在正文和活动区.
        self._refresh()

    def _flush_lines(self) -> None:
        unposted = self._buffer[self._committed :]
        index = unposted.rfind("\n")
        if index == -1:
            return
        for line in unposted[:index].split("\n"):
            self._emit_answer(line)
        self._committed += index + 1

    def _emit_answer(self, line: str) -> None:
        if self._block_first and not line.strip():
            # 块开头的空行不占 "●": 模型常以换行起手, 让标记落在空行上, 正文就没标记了.
            return
        self._console.print(assistant_line(line, first=self._block_first))
        self._block_first = False
        self._emitted_first = True

    def _refresh(self) -> None:
        if self._live is None or self._suspended:
            return
        remainder = self._buffer[self._committed :]
        if remainder:
            self._live.update(assistant_line(remainder, first=self._block_first))
        else:
            self._live.update(Text(self._status, style=_STATUS_STYLE))

    def _suspend(self) -> None:
        if self._live is None:
            return
        self.finish()
        self._suspended = True

    def _resume(self) -> None:
        self._suspended = False
        self._refresh()


_HANDLERS = {
    K.STEP_STARTED: TerminalRunRenderer._on_step,
    K.MODEL_REASONING_STATUS: TerminalRunRenderer._on_reasoning,
    K.MODEL_OUTPUT_DELTA: TerminalRunRenderer._on_output_delta,
    K.MODEL_COMPLETED: TerminalRunRenderer._on_model_completed,
    K.CONTEXT_COMPACTED: TerminalRunRenderer._on_context_compacted,
    K.MODEL_USAGE: TerminalRunRenderer._on_model_usage,
    K.MODEL_FAILED: TerminalRunRenderer._on_model_failed,
    K.TOOL_QUEUED: TerminalRunRenderer._on_tool_queued,
    K.TOOL_PREPARED: TerminalRunRenderer._on_tool_prepared,
    K.POLICY_RESOLVED: TerminalRunRenderer._on_policy,
    K.APPROVAL_REQUESTED: TerminalRunRenderer._on_approval_requested,
    K.APPROVAL_RESOLVED: TerminalRunRenderer._on_approval_resolved,
    K.TOOL_COMPLETED: TerminalRunRenderer._on_tool_completed,
    K.TOOL_CANCELLED: TerminalRunRenderer._on_tool_completed,
    K.TURN_COMPLETED: TerminalRunRenderer._on_turn_end,
    K.TURN_CANCELLED: TerminalRunRenderer._on_turn_end,
    K.TURN_FAILED: TerminalRunRenderer._on_turn_end,
}
