"""interfaces 层：append-only 流式渲染、取消灰色追加、SIGINT -> CancelToken。

方案 D（逐行提交）：正文逐行 append 进滚动历史，Live 区只留当前半行；收尾提示
（取消 / 失败）由 repl 在 turn 后追加。这里验证 append 行为、无重复、取消灰色标记，
以及 SIGINT 接线。
"""

from __future__ import annotations

import signal
from pathlib import Path

from rich.console import Console

from forgecli.application.agent_loop import (
    AgentLoop,
    AnswerAction,
    LoopDecision,
    LoopInput,
    LoopObservation,
    LoopStepResult,
    LoopStop,
    LoopStopReason,
)
from forgecli.application.agent_turn import AgentTurnService, TurnCancelSource
from forgecli.application.intent_router import IntentRouter
from forgecli.application.llm.gateway import CancelToken
from forgecli.application.session import EventType, SessionService
from forgecli.application.slash_commands import CommandRegistry
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import Repl, _install_sigint
from forgecli.interfaces.cli.stream_render import StreamingTranscript
from forgecli.interfaces.cli.transcript import assistant_notice

_NOTICE_STYLE = "#6c7086"


class _EchoLoop(AgentLoop):
    """非流式脚本 loop：不经 gateway，直接给答案（无 on_delta）。"""

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        return LoopDecision(next_action=AnswerAction(text="好的"))

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        return LoopStop.of(LoopStopReason.FINAL_ANSWER)


def _visible_lines(console: Console) -> list[str]:
    return [line for line in console.export_text().split("\n") if line.strip()]


# ---- StreamingTranscript：逐行提交 ----


def test_streaming_transcript_commits_lines_append_only() -> None:
    console = Console(record=True, width=80)
    view = StreamingTranscript(console)

    view.feed("turn 外")  # no-op：不在 turn 内
    with view.turn():
        view.feed("第一行\n第二")  # 提交 "● 第一行"，半行 "第二"
        view.feed("行\n第三行")  # 提交 "  第二行"，半行 "第三行"
        view.finish()  # 提交 "  第三行"

    assert _visible_lines(console) == ["● 第一行", "  第二行", "  第三行"]
    assert "turn 外" not in console.export_text()


def test_finish_without_trailing_newline_commits_last_half_line() -> None:
    console = Console(record=True, width=80)
    view = StreamingTranscript(console)

    with view.turn():
        view.feed("只有半行没有换行")
        assert view.had_output is False  # 半行未提交前 had_output 为假
        view.finish()

    assert view.had_output is True
    assert view.rendered_text == "只有半行没有换行"
    assert _visible_lines(console) == ["● 只有半行没有换行"]


# ---- 全链路：流式正文 append + 取消灰色追加，无重复 ----


class _StreamingLoop(AgentLoop):
    """脚本流式 loop：start 时把 delta 喂给 on_delta，再按 outcome 结束。"""

    def __init__(
        self,
        on_delta,
        deltas: tuple[str, ...],
        stop: LoopStop,
        *,
        answer: str | None = None,
    ) -> None:
        self._on_delta = on_delta
        self._deltas = deltas
        self._stop = stop
        self._answer = answer
        self.partial_answer: str | None = None
        self.usage_drafts: tuple[object, ...] = ()

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        for delta in self._deltas:
            self._on_delta(delta)
        self.partial_answer = "".join(self._deltas) or None
        if self._answer is not None:
            return LoopDecision(next_action=AnswerAction(text=self._answer))
        return self._stop

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        return LoopStop.of(LoopStopReason.FINAL_ANSWER)


def _repl(
    console: Console,
    session: SessionService,
    agent_turn: AgentTurnService,
    cancel_source: TurnCancelSource,
    stream_view: StreamingTranscript,
) -> Repl:
    registry = CommandRegistry()
    return Repl(
        console,
        IntentRouter(registry=registry),
        registry,
        RichOutput(console),
        session,
        agent_turn,
        stream_view=stream_view,
        cancel_source=cancel_source,
    )


def _session(tmp_path: Path) -> SessionService:
    sessions = tmp_path / "sessions"
    service = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        id_factory=lambda: "sid",
    )
    service.start()
    return service


def test_streamed_turn_appends_body_once_with_blank_separator(tmp_path: Path) -> None:
    console = Console(record=True, width=80)
    session = _session(tmp_path)
    view = StreamingTranscript(console)
    agent_turn = AgentTurnService(
        session,
        loop_factory=lambda: _StreamingLoop(
            view.feed,
            ("aaa", "bbb"),
            LoopStop.of(LoopStopReason.FINAL_ANSWER),
            answer="aaabbb",
        ),
    )
    repl = _repl(console, session, agent_turn, TurnCancelSource(), view)

    repl._process_line("hi")

    text = console.export_text()
    # 正文只出现一次（流式 append，收尾不再重印）。
    assert text.count("aaabbb") == 1
    assert "● aaabbb" in text


def test_cancelled_turn_appends_grey_notice_without_duplicating_body(
    tmp_path: Path,
) -> None:
    console = Console(record=True, width=80)
    session = _session(tmp_path)
    view = StreamingTranscript(console)
    agent_turn = AgentTurnService(
        session,
        loop_factory=lambda: _StreamingLoop(
            view.feed,
            ("已生成一半",),
            LoopStop.of(LoopStopReason.USER_CANCELLED, message="本轮回复已取消。"),
        ),
    )
    repl = _repl(console, session, agent_turn, TurnCancelSource(), view)

    repl._process_line("讲个故事")

    text = console.export_text()
    # 正文 append 一次，取消标记换行追加，正文不重复。
    assert text.count("已生成一半") == 1
    assert "● 已生成一半" in text
    assert "（本轮回复已被用户取消）" in text
    # 落盘仍是 正文 + 标记 的完整文本。
    events = JsonlEventStore(tmp_path / "sessions").read("sid")
    assistant = next(e for e in events if e.type is EventType.ASSISTANT_MESSAGE)
    assert assistant.payload["text"] == "已生成一半\n（本轮回复已被用户取消）"
    assert assistant.payload["status"] == "failed"


def test_non_streaming_completed_turn_prints_body_and_clears_source(
    tmp_path: Path,
) -> None:
    console = Console(record=True, width=80)
    session = _session(tmp_path)
    cancel_source = TurnCancelSource()
    agent_turn = AgentTurnService(session, loop_factory=_EchoLoop)
    repl = _repl(
        console, session, agent_turn, cancel_source, StreamingTranscript(console)
    )

    repl._process_line("你好")

    events = JsonlEventStore(tmp_path / "sessions").read("sid")
    assert [e.type for e in events] == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
    ]
    assert cancel_source.current() is None  # 一轮结束后取消源清空
    assert "好的" in console.export_text()


# ---- 收尾提示样式 ----


def test_assistant_notice_is_grey_and_indented() -> None:
    notice = assistant_notice("（本轮回复已被用户取消）")
    plain = notice.plain
    assert plain.startswith("  ")  # 悬挂缩进对齐正文列
    # 文本段落用灰色样式（非助手正文青绿）。
    styles = {str(span.style) for span in notice.spans}
    assert _NOTICE_STYLE in styles


# ---- SIGINT 接线 ----


def test_install_sigint_cancels_token_and_restores_handler() -> None:
    previous = signal.getsignal(signal.SIGINT)
    token = CancelToken()
    restore = _install_sigint(token)
    try:
        signal.raise_signal(signal.SIGINT)  # 不应抛 KeyboardInterrupt
        assert token.cancelled is True
    finally:
        restore()
    assert signal.getsignal(signal.SIGINT) is previous
