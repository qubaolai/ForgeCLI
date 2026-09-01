"""处理过程跨进程留存 (`sessions/<id>/runs.jsonl`).

内存缓冲只有 2048 条且随进程消失, 而"回看上周那一轮到底做了什么"要的正是跨进程的那份.

分工必须守住: 这份是**给人看的过程**, 不是恢复真相源 (ADR-0016 §9). 会话正文由
`events.jsonl` 提供. 所以这里每一条用例都在问同一件事的两面 —— 正常时留得住, 出错时
不要连累会话.
"""

from __future__ import annotations

import json
from pathlib import Path

from forgecli.domain.agent.run_events import (
    AgentRunEvent,
    AgentRunEventKind,
    TextDeltaPayload,
    TurnFinishedPayload,
    TurnStartedPayload,
)
from forgecli.infrastructure.session.jsonl_run_store import (
    MAX_STORED_OUTPUT,
    JsonlRunStore,
)


def _store(tmp_path: Path, session: str = "s1") -> JsonlRunStore:
    return JsonlRunStore(tmp_path, lambda: session)


def _event(kind: AgentRunEventKind, payload: object, *, turn: str = "t1", seq: int = 1):
    return AgentRunEvent(
        event_id=f"e{seq}",
        kind=kind,
        turn_id=turn,
        sequence=seq,
        occurred_at=0.0,
        payload=payload,  # type: ignore[arg-type]
        request_id="r1",
    )


def _delta(text: str, *, turn: str = "t1", seq: int = 2) -> AgentRunEvent:
    return _event(
        AgentRunEventKind.MODEL_OUTPUT_DELTA,
        TextDeltaPayload(text=text),
        turn=turn,
        seq=seq,
    )


def _finish(turn: str = "t1", seq: int = 9) -> AgentRunEvent:
    return _event(
        AgentRunEventKind.TURN_COMPLETED,
        TurnFinishedPayload(status="completed"),
        turn=turn,
        seq=seq,
    )


def test_a_finished_turn_is_written_once_with_its_deltas_folded(tmp_path: Path) -> None:
    """逐条落盘增量既撑大文件也让读回来的人再拼一遍, 而它们的信息量等于拼好的那段."""
    store = _store(tmp_path)
    store.on_event(
        _event(
            AgentRunEventKind.TURN_STARTED,
            TurnStartedPayload(mode="auto", tool_count=3),
        )
    )
    store.on_event(_delta("我先", seq=2))
    store.on_event(_delta("看看目录", seq=3))
    store.on_event(_finish())

    lines = (tmp_path / "s1" / "runs.jsonl").read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1, "一轮一行, 收尾时写一次"
    snapshot = json.loads(lines[0])
    assert snapshot["turn_id"] == "t1"
    assert snapshot["outputs"]["r1"] == "我先看看目录"
    # 增量不逐条留在 events 里, 只剩 turn_started 与 turn_completed.
    kinds = [item["kind"] for item in snapshot["events"]]
    assert "model_output_delta" not in kinds


def test_an_unfinished_turn_is_not_written(tmp_path: Path) -> None:
    """没收尾就没有"这一轮做了什么"这个结论, 写半截只会让回看的人以为它就是全部."""
    store = _store(tmp_path)
    store.on_event(_delta("写了一半"))

    assert not (tmp_path / "s1" / "runs.jsonl").exists()


def test_reading_it_back_gives_the_same_shape_the_page_renders(tmp_path: Path) -> None:
    """与 Web 侧的内存快照同一个形状, 前端因此不需要为历史的和进行中的各写一套渲染."""
    store = _store(tmp_path)
    store.on_event(_delta("正文"))
    store.on_event(_finish())

    restored = store.read("s1")

    assert len(restored) == 1
    assert set(restored[0]) == {"turn_id", "events", "outputs"}


def test_an_unreadable_file_does_not_take_the_session_down(tmp_path: Path) -> None:
    """一份坏掉的展示数据不该让会话打不开 —— 那是拿真东西赔一个展示品."""
    session_dir = tmp_path / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "runs.jsonl").write_text("{不是 JSON\n", encoding="utf-8")

    assert _store(tmp_path).read("s1") == []


def test_an_unwritable_directory_does_not_raise(tmp_path: Path) -> None:
    """让一次磁盘故障中断正在跑的 turn, 是拿真东西去赔一个展示品."""
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("", encoding="utf-8")
    store = JsonlRunStore(blocked, lambda: "s1")

    store.on_event(_delta("正文"))
    store.on_event(_finish())  # 不抛


def test_a_very_long_answer_is_capped(tmp_path: Path) -> None:
    """再长的正文对"回看过程"没有增量价值, 而它会让长会话的文件涨到几十 MB."""
    store = _store(tmp_path)
    store.on_event(_delta("x" * (MAX_STORED_OUTPUT + 500)))
    store.on_event(_finish())

    assert len(store.read("s1")[0]["outputs"]["r1"]) == MAX_STORED_OUTPUT


def test_turns_without_a_terminal_event_do_not_pile_up(tmp_path: Path) -> None:
    """取消, 崩溃与上游异常都可能让终态缺席; 它们不能永远占着内存."""
    store = _store(tmp_path)
    for index in range(10):
        store.on_event(_delta("x", turn=f"t{index}", seq=index + 1))

    assert len(store._pending) <= 3  # noqa: SLF001


def test_a_terminal_event_for_an_unknown_turn_writes_nothing(tmp_path: Path) -> None:
    """终态可能来两次: 循环自己发一次, 驱动抛异常时 AgentTurnService 再补一次.

    第二次到达时这一轮已经写出去了. 为它新建一个只有一条事件的条目再写一行, 会让读回来
    的那份**覆盖掉真正有内容的那一行** —— /runs 按 turn_id 去重, 后写的赢。
    """
    store = _store(tmp_path)
    store.on_event(_delta("真正的正文"))
    store.on_event(_finish())

    store.on_event(_finish(seq=10))  # 补发的第二条终态

    snapshots = store.read("s1")
    assert len(snapshots) == 1
    assert snapshots[0]["outputs"]["r1"] == "真正的正文"
