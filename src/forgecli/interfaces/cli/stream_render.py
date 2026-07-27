"""流式输出渲染：把 gateway 的增量文本 append-only 逐行提交进终端滚动历史。

StreamingTranscript 是装配方交给 BuiltinAgentLoop 的 on_delta 消费端（bound
method feed）。设计遵循终端原生模型（append + scrollback）：

    - 每收到一段增量，凑齐的完整行立刻 console.print 永久提交（append 到 Live 区
      上方、进入滚动历史）；尚未换行的半行留在 Live 区实时刷新。
    - turn 结束时 finish() 把最后半行定稿提交、清空 Live 区。
    - 因此 Live 区永远只有当前一行高，无论回复多长都不会超出屏幕高度、不会被
      Rich 的 vertical_overflow 裁掉，也不产生"擦除+重印"式的滚动历史重复。

收尾装饰（取消标记 / 失败说明）不在这里——正文由本类逐行提交，收尾提示由 repl
在 turn 之后按终态追加（见 repl._finish_render）。非 TTY（管道 / 测试）下 Rich
Live 不渲染，print 直接输出，逐行提交行为不变。
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from rich.console import Console
from rich.live import Live
from rich.text import Text

from forgecli.interfaces.cli.transcript import assistant_line

_PLACEHOLDER = "● 处理中..."
_PLACEHOLDER_STYLE = "dim #94e2d5"


class StreamingTranscript:
    """一轮助手回复的 append-only 流式渲染器；单线程 REPL：同一时刻至多一轮。"""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._live: Live | None = None
        self._buffer = ""  # 本轮 feed 的全部正文
        self._committed_len = 0  # 已 append 提交的字符数（buffer 偏移，含行末换行）
        self._emitted_first = False  # 是否已提交过全局第一行（决定 "●" vs 缩进）

    @contextmanager
    def turn(self) -> Generator[None]:
        """包住一轮处理：Live 区显示当前半行；退出时擦除（半行已由 finish 提交）。"""
        self._buffer = ""
        self._committed_len = 0
        self._emitted_first = False
        live = Live(
            Text(_PLACEHOLDER, style=_PLACEHOLDER_STYLE),
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

    def feed(self, delta: str) -> None:
        """接收一段增量：凑齐的完整行立即提交，半行留 Live 区。turn 外为 no-op。"""
        if self._live is None:
            return
        self._buffer += delta
        self._flush_complete_lines()
        self._refresh_live()

    def finish(self) -> None:
        """定稿：把 Live 区剩余半行作为最后一行提交，清空 Live 区。turn 外为 no-op。"""
        if self._live is None:
            return
        remainder = self._buffer[self._committed_len :]
        if remainder:
            self._emit_line(remainder)
            self._committed_len = len(self._buffer)
        # 半行已提交到 Live 上方；清空 Live 区，退出 turn 时擦除即无残留、无重复。
        self._live.update(Text(""))

    @property
    def had_output(self) -> bool:
        """本轮是否提交过至少一行正文（用于 repl 区分流式轮与无输出轮）。"""
        return self._emitted_first

    @property
    def rendered_text(self) -> str:
        """已提交的正文（finish 后即全部 feed 文本）；repl 据此算收尾追加的尾巴。"""
        return self._buffer[: self._committed_len]

    # ---- 内部 ----

    def _flush_complete_lines(self) -> None:
        unposted = self._buffer[self._committed_len :]
        idx = unposted.rfind("\n")
        if idx == -1:
            return
        block = unposted[:idx]  # 已换行的完整行（不含末换行），可能多行
        for line in block.split("\n"):
            self._emit_line(line)
        self._committed_len += idx + 1

    def _emit_line(self, line: str) -> None:
        self._console.print(assistant_line(line, first=not self._emitted_first))
        self._emitted_first = True

    def _refresh_live(self) -> None:
        if self._live is None:
            return
        remainder = self._buffer[self._committed_len :]
        if remainder:
            self._live.update(assistant_line(remainder, first=not self._emitted_first))
        elif not self._emitted_first:
            self._live.update(Text(_PLACEHOLDER, style=_PLACEHOLDER_STYLE))
        else:
            self._live.update(Text(""))
