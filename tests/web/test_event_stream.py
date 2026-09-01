"""SSE 事件流的收尾行为：不能拖住本地服务停止。"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    ModelStartedPayload,
    TextDeltaPayload,
    TurnFinishedPayload,
    TurnStartedPayload,
)
from forgecli.interfaces.web.app import create_app
from forgecli.interfaces.web.events import WebEventHub


class FakeRegistry:
    def __init__(self, hub: WebEventHub) -> None:
        self.projects = SimpleNamespace(list_trusted=tuple)
        # runs 落盘为空: 这组用例问的是"内存里那一轮怎么折叠", 历史那一份由
        # tests/web/test_run_history.py 单独覆盖。
        self.active = SimpleNamespace(
            events=hub,
            runs=SimpleNamespace(read=lambda session_id: []),
            session=SimpleNamespace(current=lambda: SimpleNamespace(session_id="s1")),
        )

    def close(self) -> None:
        return None


class EventStream:
    """直接按 ASGI 协议打开一条 SSE 流。

    httpx 的 ``ASGITransport`` 会把整个响应体先跑完再返回，测不了"流在什么时候结束"，
    而这正是这组回归要断言的东西。
    """

    def __init__(self, app: Any, hub: WebEventHub) -> None:
        self._app = app
        self._hub = hub
        self._chunks: list[str] = []
        self._connected = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.status = 0

    async def __aenter__(self) -> EventStream:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/events",
            "raw_path": b"/api/v1/events",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"127.0.0.1"),
                (
                    b"cookie",
                    f"forge_web_session={self._app.state.security.session}".encode(),
                ),
            ],
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 8000),
        }
        self._task = asyncio.create_task(self._app(scope, self._receive, self._send))
        await asyncio.wait_for(self._connected.wait(), timeout=3.0)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _receive(self) -> dict[str, object]:
        # 客户端一直挂着：这条流只能被服务端自己收尾。
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.status = int(message["status"])
            self._connected.set()
        elif message["type"] == "http.response.body":
            self._chunks.append(bytes(message.get("body", b"")).decode())

    @property
    def text(self) -> str:
        return "".join(self._chunks)

    async def read_until(self, marker: str, *, limit: float = 3.0) -> str:
        async def poll() -> None:
            while marker not in self.text:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(poll(), timeout=limit)
        return self.text

    async def wait_closed(self, *, limit: float = 3.0) -> None:
        assert self._task is not None
        await asyncio.wait_for(asyncio.shield(self._task), timeout=limit)


def _app(hub: WebEventHub, static_dir: Any) -> Any:
    return create_app(
        registry=FakeRegistry(hub),  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=static_dir,
    )


def test_event_stream_ends_when_local_server_stops(tmp_path) -> None:
    """回归：Ctrl-C 时 SSE 流必须自己收尾，否则 Uvicorn 会一直等连接关闭。"""

    async def scenario() -> str:
        hub = WebEventHub()
        app = _app(hub, tmp_path)
        async with EventStream(app, hub) as stream:
            assert stream.status == 200
            app.state.stopping.set()
            await stream.wait_closed()
            return stream.text

    assert "event: server_stopping" in asyncio.run(scenario())


def test_event_stream_forwards_run_events_with_kind(tmp_path) -> None:
    """页面按单一 ``run_event`` 名订阅，事件类型放在 data 里，避免漏订阅新事件类型。"""

    async def scenario() -> str:
        hub = WebEventHub()
        bus = AgentRunEventBus()
        bus.subscribe(hub)
        app = _app(hub, tmp_path)
        async with EventStream(app, hub) as stream:
            # 从工作线程发布，验证跨线程唤醒确实回到了流所在的事件循环。
            worker = threading.Thread(
                target=bus.publish,
                args=(AgentRunEventKind.TURN_STARTED,),
                kwargs={
                    "turn_id": "turn_0001",
                    "payload": TurnStartedPayload(mode="plan", tool_count=2),
                },
            )
            worker.start()
            worker.join()
            return await stream.read_until("turn_started")

    body = asyncio.run(scenario())
    assert "event: run_event" in body
    assert '"kind":"turn_started"' in body


def test_event_stream_ends_when_project_runtime_closes(tmp_path) -> None:
    """切换项目会关闭旧 Runtime；旧流应结束，让浏览器重连到新 Runtime。"""

    async def scenario() -> None:
        hub = WebEventHub()
        app = _app(hub, tmp_path)
        async with EventStream(app, hub) as stream:
            hub.close()
            await stream.wait_closed()

    asyncio.run(scenario())


def test_hub_wait_returns_on_stop_without_new_events() -> None:
    async def scenario() -> float:
        hub = WebEventHub()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, stop.set)
        started = loop.time()
        await hub.wait(0, timeout=30.0, stop=stop)
        return loop.time() - started

    assert asyncio.run(scenario()) == pytest.approx(0.05, abs=0.5)


def test_event_stream_keeps_up_with_a_burst_of_deltas(tmp_path) -> None:
    """回归：一次长回答的增量洪峰必须照常送达，不能把流堵住。"""

    async def scenario() -> str:
        hub = WebEventHub()
        bus = AgentRunEventBus()
        bus.subscribe(hub)
        app = _app(hub, tmp_path)
        async with EventStream(app, hub) as stream:
            worker = threading.Thread(target=_flood, args=(bus, 1500))
            worker.start()
            worker.join()
            body = await stream.read_until("id: 1500", limit=10.0)
            app.state.stopping.set()
            await stream.wait_closed()
            return body

    body = asyncio.run(scenario())
    assert body.count("event: run_event") == 1500


def _flood(bus: AgentRunEventBus, count: int) -> None:
    for _ in range(count):
        bus.publish(
            AgentRunEventKind.MODEL_OUTPUT_DELTA,
            turn_id="turn_0001",
            payload=TextDeltaPayload(text="字"),
            request_id="req-1",
        )


def test_run_snapshot_folds_deltas_into_one_text_per_model_call(tmp_path) -> None:
    """刷新页面要能重看处理过程，但不能为此拖回上千条增量。"""
    hub = WebEventHub()
    bus = AgentRunEventBus()
    bus.subscribe(hub)
    bus.publish(
        AgentRunEventKind.MODEL_STARTED,
        turn_id="turn_0001",
        payload=ModelStartedPayload(call_index=0, provider="demo", model="demo-1"),
        request_id="req-1",
    )
    _flood(bus, 800)
    bus.publish(
        AgentRunEventKind.TURN_COMPLETED,
        turn_id="turn_0001",
        payload=TurnFinishedPayload(status="completed", elapsed_ms=12.0),
    )
    app = _app(hub, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/boot?token=known-token", follow_redirects=False)
        payload = client.get("/api/v1/runs").json()

    items = payload["items"]
    assert [item["turn_id"] for item in items] == ["turn_0001"]
    # 800 条增量折叠成一段正文，事件里只留下结构性的两条。
    assert [event["kind"] for event in items[0]["events"]] == [
        "model_started",
        "turn_completed",
    ]
    assert items[0]["outputs"] == {"req-1": "字" * 800}
