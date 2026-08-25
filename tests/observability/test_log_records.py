"""日志记录的形状 (ADR-0035 决策 2).

这组用例守的不是"有没有打日志", 而是**日志能不能被查**: 一条记录一行, 键值可 grep,
运行上下文自动带上, 关掉级别之后一个字符串都不构造.
"""

from __future__ import annotations

import logging

import pytest

from forgecli.shared.observability.context import bind, current, update
from forgecli.shared.observability.log import get_log, set_max_value_chars
from forgecli.shared.observability.metrics import METRICS


def test_fields_render_as_key_value(records: pytest.LogCaptureFixture) -> None:
    get_log("t").info("tool.requested", tool="shell_run", exit_code=0, ok=True)

    message = records.records[-1].getMessage()
    assert message == "tool.requested tool=shell_run exit_code=0 ok=True"


def test_multiline_values_stay_on_one_line(records: pytest.LogCaptureFixture) -> None:
    """换行必须被转义: 一条记录跨两行, grep 出来的永远是半截."""
    get_log("t").info("shell.exited", stderr="第一行\n第二行")

    message = records.records[-1].getMessage()
    assert "\n" not in message
    assert message == 'shell.exited stderr="第一行\\n第二行"'


def test_run_context_is_attached_without_passing_it(
    records: pytest.LogCaptureFixture,
) -> None:
    with bind(session_id="s_1", turn_id="turn_0007"):
        get_log("t").info("turn.received")

    assert records.records[-1].getMessage() == (
        "turn.received session=s_1 turn=turn_0007"
    )


def test_bind_restores_exactly_on_exit() -> None:
    with bind(session_id="s_1"):
        with bind(turn_id="t_1", tool="fs_read"):
            assert current().session_id == "s_1"  # 嵌套是累加, 不是覆盖
            assert current().tool == "fs_read"
        assert current().tool == ""
    assert current().session_id == ""


def test_update_leaks_no_further_than_the_enclosing_bind() -> None:
    """`update` 没有还原点, 但外层 bind 退出时把它一起抹掉."""
    with bind(session_id="s_1"):
        update(step=3, request_id="req_abc")
        assert current().step == 3
    assert current().step == 0
    assert current().request_id == ""


def test_long_values_are_truncated_with_the_dropped_count(
    records: pytest.LogCaptureFixture,
) -> None:
    set_max_value_chars(20)
    try:
        get_log("t").info("tool.output", text="y" * 100)
    finally:
        set_max_value_chars(4000)

    message = records.records[-1].getMessage()
    assert message.endswith("...(+82 chars)")  # 20 个字符 + 两个引号被留下


def test_disabled_level_builds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """级别没开时连字符串都不该拼: 详细日志的字段本身就很贵."""
    logging.getLogger("forgecli.t").setLevel(logging.INFO)
    exploded = False

    class _Boom:
        def __str__(self) -> str:
            nonlocal exploded
            exploded = True
            return "boom"

    get_log("t").debug("tool.output", text=_Boom())
    assert exploded is False


def test_span_records_duration_and_outcome(
    records: pytest.LogCaptureFixture,
) -> None:
    METRICS.reset()
    log = get_log("t")

    with log.span("tool.execute", tool="shell_run") as span:
        span.set(exit_code=0)

    message = records.records[-1].getMessage()
    assert message.startswith("tool.execute.ok ")
    assert "elapsed_ms=" in message
    assert "exit_code=0" in message
    assert METRICS.snapshot()["counters"] == {"tool.execute.ok": 1}


def test_span_reraises_and_logs_the_failure(
    records: pytest.LogCaptureFixture,
) -> None:
    METRICS.reset()
    log = get_log("t")

    with pytest.raises(RuntimeError), log.span("tool.execute"):
        raise RuntimeError("命令没跑起来")

    message = records.records[-1].getMessage()
    assert message.startswith("tool.execute.error ")
    assert "error=RuntimeError" in message
    assert records.records[-1].exc_info is not None  # traceback 必须留下
    assert METRICS.snapshot()["counters"] == {
        "tool.execute.error{error=RuntimeError}": 1
    }
