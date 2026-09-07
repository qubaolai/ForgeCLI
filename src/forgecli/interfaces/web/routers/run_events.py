"""运行事件的两条出口: 增量流 (SSE) 与快照 (``/runs``)。

放在一个模块里, 因为它们**必须共用同一套续传规则** (ADR-0048 决策 2): 快照带回自己的
水位, 流从那个水位往下接。分成两处写, 两边对"位置"的理解迟早会分叉, 而分叉的表现是
页面少几条事件 —— 没有任何报错。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Request
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from forgecli.domain.agent.run_events import AgentRunEventKind
from forgecli.infrastructure.session.jsonl_run_store import MAX_STORED_OUTPUT
from forgecli.interfaces.runtime.event_hub import ResumePoint, RunEventHub
from forgecli.interfaces.web.deps import active_runtime, stopping

router = APIRouter(prefix="/api/v1")

# 心跳间隔: 没有事件时每隔这么久发一个注释帧, 让中间的代理不要把连接当成死的.
# 整数是因为 EventSourceResponse.ping 的类型就是 int.
_STREAM_KEEP_ALIVE_SECONDS = 15


def _frame(resume: ResumePoint, name: str, data: str) -> ServerSentEvent:
    """一条 SSE 事件. framing 交给 sse-starlette (ADR-0040 决策 4.7).

    换掉手写 f-string 不只是少写三个字段: `data` 里只要出现一个换行, 手写版本就会拼出
    一条被浏览器截断的事件, 而 `ServerSentEvent` 会按协议把多行拆成多条 `data:`.
    我们现在的 data 是紧凑 JSON 所以撞不上, 但那是巧合, 不是保证.

    ``id`` 是"哪个实例的第几条"而不是一个裸游标 (ADR-0048 决策 2): 浏览器会把它原样
    放进重连的 ``Last-Event-ID``, 而只有带上实例, 服务端才判得出这个位置是不是自己的.
    """
    return ServerSentEvent(id=resume.token(), event=name, data=data)


def _turn_snapshots(hub: RunEventHub, session_id: str) -> list[dict[str, object]]:
    """把某个会话的缓冲事件按 turn 归档，并把流式增量折叠成每次模型调用的一段正文。

    逐条返回 ``model_output_delta`` 会让一次刷新拖回几百 KB，页面还要再拼一遍；
    折叠之后一个长 turn 也只剩几十条事件。

    ``session_id`` 必传 (ADR-0048 决策 2): 缓冲跨会话共用, 而按 turn 归档的键在两个
    会话之间会撞 —— 不过滤就会把上一个会话的 ``turn_0001`` 与这一个的叠在一起。
    """
    turns: dict[str, dict[str, object]] = {}
    for item in hub.buffered(session_id):
        turn_id = str(item.data.get("turn_id", ""))
        entry = turns.setdefault(
            turn_id,
            {"session_id": session_id, "turn_id": turn_id, "events": [], "outputs": {}},
        )
        if item.kind != AgentRunEventKind.MODEL_OUTPUT_DELTA.value:
            events = entry["events"]
            assert isinstance(events, list)
            events.append(item.data)
            continue
        payload = item.data.get("payload")
        text = payload.get("text", "") if isinstance(payload, dict) else ""
        outputs = entry["outputs"]
        assert isinstance(outputs, dict)
        key = str(item.data.get("request_id") or "")
        outputs[key] = f"{outputs.get(key, '')}{text}"[:MAX_STORED_OUTPUT]
    return list(turns.values())


@router.get("/events")
async def events(
    request: Request,
    after: str | None = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    hub = active_runtime(request).events
    stop = stopping(request)
    # 只有断线重连才补发历史；首次连接从当前位置开始，已完成 turn 的正文由
    # transcript 提供，不能靠重放事件缓冲拼出来。页面自己管重连节奏，所以除了
    # EventSource 自带的 Last-Event-ID，也接受显式的 after 查询参数。
    raw_resume = after if after is not None else last_event_id
    parsed = ResumePoint.parse(raw_resume)
    # 认不出来的续传标识 (首次连接, 或旧版的裸数字) 从当前水位开始, 不补发历史。
    position = hub.resume_point() if parsed is None else parsed

    async def stream() -> AsyncIterator[ServerSentEvent | bytes]:
        """一条 SSE 流；服务停止或客户端断开时立即收尾，不拖住优雅退出。

        断线检测与心跳都归 ``EventSourceResponse``：它自己监听 ``receive`` 上的
        ``http.disconnect`` 并取消这个生成器，也自己按固定间隔发注释帧。原先这两件
        事都挂在下面这个循环上，代价是它们的精度被 ``hub.wait`` 的超时绑死——没有
        事件时，断开要等满一整个心跳周期才发现。
        """
        nonlocal position
        while not stop.is_set():
            resync, pending = hub.after(position)
            if resync:
                # 实例不匹配 / 游标超前 / 缓冲过期, 三种一个处置: 让页面重新取一次
                # 快照, 再从快照带回的水位接着走 (ADR-0048 决策 2)。
                position = hub.resume_point()
                yield _frame(
                    position,
                    "resync_required",
                    '{"reason":"resume_point_unusable"}',
                )
                continue
            if pending:
                # 一次 yield 送完这批：逐条 yield 会让一次长回答变成上千次小写入。
                # bytes 被 sse-starlette 原样送出，所以批量不必牺牲协议正确性。
                frames = []
                for item in pending:
                    position = ResumePoint(hub.stream_id, item.cursor)
                    data = json.dumps(
                        item.data, ensure_ascii=False, separators=(",", ":")
                    )
                    frames.append(_frame(position, "run_event", data).encode())
                yield b"".join(frames)
                continue
            if hub.closed:
                # 项目被切换或关闭：结束这条流，浏览器会重连到新的 Runtime。
                return
            await hub.wait(
                position.cursor, timeout=_STREAM_KEEP_ALIVE_SECONDS, stop=stop
            )
        # 让页面知道这是本地服务主动停止，而不是需要重连的网络抖动。
        yield _frame(position, "server_stopping", '{"reason":"local_shutdown"}')

    return EventSourceResponse(stream(), ping=_STREAM_KEEP_ALIVE_SECONDS)


@router.get("/runs")
async def runs(request: Request) -> dict[str, object]:
    """当前会话的处理过程，按 turn 分组。

    两个来源合并: 已经落盘的历史轮次 (`runs.jsonl`) 加上进程内还没收尾的那一轮。
    **落盘的在前**，因为它们先发生；同一个 turn 以内存那份为准，它更新。

    它仍然不是恢复真相源 (ADR-0016 §9)：这份丢了只是历史过程展不开，会话正文由
    transcript 提供。
    """
    runtime = active_runtime(request)
    session_id = runtime.session.current().session_id
    # 先取水位再读内容 (ADR-0048 决策 2): 反过来的话, 这两句之间产生的事件既不在
    # 快照里, 又落在水位之前, 于是客户端两边都拿不到它。水位偏早只会让客户端把几条
    # 已经在快照里的事件再应用一次, 而事件按 (会话, 轮次, 序号) 幂等。
    watermark = runtime.events.resume_point()
    stored = runtime.runs.read(session_id)
    live = _turn_snapshots(runtime.events, session_id)
    merged: dict[str, dict[str, object]] = {
        str(item.get("turn_id", "")): item for item in stored
    }
    for item in live:
        merged[str(item.get("turn_id", ""))] = item
    return {
        "items": list(merged.values()),
        "session_id": session_id,
        "resume": watermark.token(),
    }
