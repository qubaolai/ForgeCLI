"""关键位置确实写了日志 (ADR-0035 决策 1).

这组用例守的是"哪些位置必须留痕". 它们看起来像在断言字符串, 实际断言的是一次真实
故障能不能被还原: 模型要了什么工具, 拿回了什么, 循环为什么停 —— 少一条, 排查就断在
那一步.

事件名一旦被这里钉住就不能随手改: 改了等于让所有按事件名写的 grep 与告警失效.
"""

from __future__ import annotations

import logging
from types import MappingProxyType

import pytest

from forgecli.domain.agent.actions import (
    LoopObservation,
    LoopStop,
    ObservationSource,
    ToolRequestAction,
)
from forgecli.domain.tool.tool_call import ToolCall
from support.loop_harness import ScriptedGateway, loop_input, loop_with, response

_ids = iter(f"call_{index}" for index in range(1, 100))


def _call(name: str, **arguments: object) -> ToolCall:
    """带自定义入参的工具调用. 共享 harness 的 call() 入参是固定的, 这组用例要断言
    的恰恰是"入参原样进了日志", 所以在这里自己造一个."""
    return ToolCall(
        tool_call_id=next(_ids), name=name, arguments=MappingProxyType(arguments)
    )


def _events(records: pytest.LogCaptureFixture) -> list[str]:
    """每行的事件名 (第一段). 断言只认事件名, 不认后面的字段顺序."""
    return [record.getMessage().split(" ", 1)[0] for record in records.records]


def _line(records: pytest.LogCaptureFixture, event: str) -> str:
    return next(
        message
        for message in (record.getMessage() for record in records.records)
        if message.startswith(f"{event} ")
    )


def test_a_tool_turn_leaves_a_readable_trail(
    records: pytest.LogCaptureFixture,
) -> None:
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_call("fs.read_file", path="a.py"),)),
            response(text="读完了"),
        ]
    )
    loop, _ = loop_with(gateway)

    step = loop.start(loop_input())
    assert isinstance(step.next_action, ToolRequestAction)
    loop.observe(LoopObservation(content="文件内容", source=ObservationSource.TOOL))

    events = _events(records)
    for expected in (
        "loop.start",  # 这一轮带着什么模式 / 工具 / 预算开始
        "model.request",  # 第几次调模型, 带了多少条消息
        "model.response",  # 停止原因与 token 用量
        "tool.requested",  # 模型要了哪个工具, 完整入参
        "tool.observed",  # 它拿回了什么
    ):
        assert expected in events, f"{expected} 不在 {events}"


def test_tool_arguments_are_logged_unredacted(
    records: pytest.LogCaptureFixture,
) -> None:
    """终端要脱敏是为了不刷屏; 日志不脱敏是为了排查 —— 两者不是同一件事."""
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(_call("shell.run", command="echo hunter2"),)),
            response(text="好了"),
        ]
    )
    loop, _ = loop_with(gateway)
    loop.start(loop_input())

    assert "hunter2" in _line(records, "tool.requested")


def test_stop_records_why_and_how_much_work_was_done(
    records: pytest.LogCaptureFixture,
) -> None:
    gateway = ScriptedGateway(responses=[response(text="直接回答")])
    loop, _ = loop_with(gateway)

    loop.start(loop_input())
    step = loop.observe(
        LoopObservation(content="answer_delivered", source=ObservationSource.CONTEXT)
    )

    assert isinstance(step, LoopStop)
    line = _line(records, "loop.stop")
    assert "reason=final_answer" in line
    assert "model_calls=1" in line


def test_repeated_identical_calls_are_logged_as_a_guard(
    records: pytest.LogCaptureFixture,
) -> None:
    """原地打转是最难从结果上看出来的一类故障: 每次调用本身都是合法的."""
    repeated = tuple(_call("fs.find", pattern="*.py") for _ in range(3))
    gateway = ScriptedGateway(
        responses=[response(tool_calls=repeated), response(text="停")]
    )
    loop, _ = loop_with(gateway)

    step = loop.start(loop_input())
    for _ in range(3):
        if isinstance(step, LoopStop):
            break
        step = loop.observe(
            LoopObservation(content="没找到", source=ObservationSource.TOOL)
        )

    assert "loop.repeat_call_rejected" in _events(records)


def test_debug_off_keeps_the_bulky_lines_out(
    records: pytest.LogCaptureFixture,
) -> None:
    """整段提示词与模型正文只在 debug 下写: info 级别下它们会把日志文件淹掉."""
    logging.getLogger("forgecli").setLevel(logging.INFO)
    records.set_level(logging.INFO, logger="forgecli")
    gateway = ScriptedGateway(responses=[response(text="回答")])
    loop, _ = loop_with(gateway)

    loop.start(loop_input())

    events = _events(records)
    assert "model.request" in events
    assert "model.request.messages" not in events
