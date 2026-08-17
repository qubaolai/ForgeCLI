"""模型正文按 request_id 分块提交 (ADR-0016 §5, §8).

一轮里模型常被调用多次: 说一句 -> 调工具 -> 再说一句. 两件事必须成立:

1. 每次调用的正文是**独立一块**, 各自带 "●", 不与另一次调用的正文拼成一段.
2. 一块正文在它后面那条过程行**之前**提交完. 否则模型调工具前说的话会落到工具结果
   下面, 读起来像是它执行完才说的 —— 输出全在, 只是顺序错了, 这种错在只断言
   "包含某段文字"的测试里根本看不出来, 所以下面一律断言**先后**.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    ModelCompletedPayload,
    RunEventPayload,
    TextDeltaPayload,
    ToolCompletedPayload,
    ToolQueuedPayload,
    TurnFinishedPayload,
)
from forgecli.interfaces.cli.run_renderer import TerminalRunRenderer

K = AgentRunEventKind

# 助手正文行的标记. 数它就能数出有几块.
_MARK = "●"


class Harness:
    def __init__(self) -> None:
        self.console = Console(
            file=io.StringIO(),
            width=100,
            record=True,
            no_color=True,
            force_terminal=False,
        )
        self.renderer = TerminalRunRenderer(self.console)
        self.bus = AgentRunEventBus()
        self.bus.subscribe(self.renderer)

    def send(
        self,
        kind: AgentRunEventKind,
        payload: RunEventPayload,
        *,
        request_id: str | None = None,
    ) -> None:
        self.bus.publish(kind, turn_id="turn-1", payload=payload, request_id=request_id)

    def say(self, text: str, *, request_id: str | None = None) -> None:
        self.send(K.MODEL_OUTPUT_DELTA, TextDeltaPayload(text), request_id=request_id)

    def model_done(self, *, request_id: str | None = None) -> None:
        self.send(
            K.MODEL_COMPLETED,
            ModelCompletedPayload(finish_reason="stop"),
            request_id=request_id,
        )

    def text(self) -> str:
        return self.console.export_text()


@pytest.fixture
def harness() -> Harness:
    return Harness()


# ---- 分块 ----


def test_two_model_calls_are_two_blocks(harness: Harness) -> None:
    """典型形状: 说一句 -> 调工具 -> 再说一句. 两句各自成块."""
    with harness.renderer.turn():
        harness.say("我先看看这个文件", request_id="req-1")
        harness.model_done(request_id="req-1")
        harness.send(K.TOOL_QUEUED, ToolQueuedPayload(tool_name="fs.read_file"))
        harness.send(
            K.TOOL_COMPLETED,
            ToolCompletedPayload(tool_name="fs.read_file", status="ok"),
        )
        harness.say("看完了, 问题在第 3 行", request_id="req-2")
        harness.model_done(request_id="req-2")
        harness.renderer.finish()

    output = harness.text()
    assert output.count(_MARK) == 2
    assert "我先看看这个文件" in output
    assert "看完了, 问题在第 3 行" in output


def test_a_new_request_id_starts_a_new_block(harness: Harness) -> None:
    """兜底路径: 供应商或循环没发 MODEL_COMPLETED, 换了 request_id 也得换块."""
    with harness.renderer.turn():
        harness.say("第一次调用说的", request_id="req-1")
        harness.say("第二次调用说的", request_id="req-2")
        harness.renderer.finish()

    output = harness.text()
    assert output.count(_MARK) == 2
    assert "第一次调用说的第二次调用说的" not in output


def test_the_same_request_id_stays_one_block(harness: Harness) -> None:
    """流式增量本来就是一片一片来的, 每片开一块就等于每片一个 "●"."""
    with harness.renderer.turn():
        for piece in ("一次", "调用", "的三段增量"):
            harness.say(piece, request_id="req-1")
        harness.renderer.finish()

    output = harness.text()
    assert output.count(_MARK) == 1
    assert "一次调用的三段增量" in output


def test_a_block_with_no_request_id_still_works(harness: Harness) -> None:
    """总线允许省略 request_id (非流式回退, 部分测试). 省了也不能把正文丢掉."""
    with harness.renderer.turn():
        harness.say("没有 request_id 的正文")
        harness.model_done()
        harness.renderer.finish()

    assert "没有 request_id 的正文" in harness.text()


# ---- 收块时机 ----


def test_model_completed_commits_the_half_line(harness: Harness) -> None:
    """收到 MODEL_COMPLETED 就定稿, 不等 turn 收尾 —— finish() 之前正文就该在了."""
    with harness.renderer.turn():
        harness.say("没有换行结尾的一句", request_id="req-1")
        assert "没有换行结尾的一句" not in harness.text()
        harness.model_done(request_id="req-1")
        assert "没有换行结尾的一句" in harness.text()
        harness.renderer.finish()


def test_the_answer_lands_above_the_tool_line(harness: Harness) -> None:
    """这就是要修的现象: 半行正文留在活动区, 被工具行顶到时间线下游去."""
    with harness.renderer.turn():
        harness.say("我去读一下文件", request_id="req-1")
        harness.model_done(request_id="req-1")
        harness.send(K.TOOL_QUEUED, ToolQueuedPayload(tool_name="fs.read_file"))
        harness.renderer.finish()

    output = harness.text()
    assert output.index("我去读一下文件") < output.index("调用 fs.read_file")


def test_a_tool_line_alone_is_enough_to_commit(harness: Harness) -> None:
    """不依赖循环的事件顺序: 没有 MODEL_COMPLETED, 第一条 TOOL_QUEUED 也要先收块."""
    with harness.renderer.turn():
        harness.say("我去读一下文件", request_id="req-1")
        harness.send(K.TOOL_QUEUED, ToolQueuedPayload(tool_name="fs.read_file"))
        harness.renderer.finish()

    output = harness.text()
    assert output.index("我去读一下文件") < output.index("调用 fs.read_file")


def test_the_answer_lands_above_the_turn_summary(harness: Harness) -> None:
    """收尾行是整轮最后一行. 最终回答排在它下面, 用户会以为回答属于下一轮."""
    with harness.renderer.turn():
        harness.say("最终回答", request_id="req-1")
        harness.model_done(request_id="req-1")
        harness.send(
            K.TURN_COMPLETED,
            TurnFinishedPayload(status="final_answer", model_calls=1, tool_calls=1),
        )
        harness.renderer.finish()

    output = harness.text()
    assert output.index("最终回答") < output.index("final_answer")


def test_committed_text_is_not_repeated_in_the_active_area(harness: Harness) -> None:
    """收块要顺带刷掉活动区, 否则同一段文字同时挂在正文和活动区上."""
    with harness.renderer.turn():
        harness.say("只说一次", request_id="req-1")
        harness.model_done(request_id="req-1")
        harness.send(K.TOOL_QUEUED, ToolQueuedPayload(tool_name="shell.run"))
        harness.renderer.finish()

    assert harness.text().count("只说一次") == 1


# ---- 标记落点 ----


def test_a_leading_blank_line_does_not_eat_the_bullet(harness: Harness) -> None:
    """模型常以换行起手. 让 "●" 落在空行上, 正文那行就成了没有标记的续行."""
    with harness.renderer.turn():
        harness.say("\n真正的第一行\n", request_id="req-1")
        harness.model_done(request_id="req-1")
        harness.renderer.finish()

    assert f"{_MARK} 真正的第一行" in harness.text()


# ---- 给收尾渲染用的 rendered_text ----


def test_rendered_text_is_the_last_block_only(harness: Harness) -> None:
    """取消轮要从 response.text 里去掉"已经打过的那段".

    而 loop 交回的 partial_answer 只是**最后一次**调用累积的文本 —— 跨块拼接反而对不上,
    去不掉的那截会以正文和灰色提示两种样式各打一遍.
    """
    with harness.renderer.turn():
        harness.say("第一次调用说的", request_id="req-1")
        harness.model_done(request_id="req-1")
        harness.send(K.TOOL_QUEUED, ToolQueuedPayload(tool_name="shell.run"))
        harness.say("第二次调用说的", request_id="req-2")
        harness.renderer.finish()

        assert harness.renderer.rendered_text == "第二次调用说的"
        assert harness.renderer.had_output
