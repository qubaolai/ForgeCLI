"""真实 API、共享 broker 与 SSE 缓冲：卡片必须在工具等待期间可见。"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.domain.human_prompt import HumanPrompt, PromptChoice, PromptKind
from forgecli.interfaces.runtime.event_hub import RunEventHub
from forgecli.interfaces.runtime.human_prompt import BlockingHumanPromptBroker
from forgecli.interfaces.runtime.project_runtime import ProjectRuntime
from forgecli.interfaces.web.app import create_app
from forgecli.shared.observability.context import bind


@pytest.mark.parametrize("skipped", [False, True])
def test_live_question_api_and_reconnect_snapshot(skipped: bool) -> None:
    runtime = object.__new__(ProjectRuntime)
    runtime.event_bus = AgentRunEventBus()
    runtime.events = RunEventHub()
    runtime.event_bus.subscribe(runtime.events)
    runtime.session = Mock()
    runtime.prompts = BlockingHumanPromptBroker(runtime._prompt_changed)
    prompt = HumanPrompt(
        "question-1",
        PromptKind.QUESTION,
        "实现哪些阶段？",
        choices=(
            PromptChoice("one", "阶段 1", "卡片"),
            PromptChoice("two", "阶段 2", "接口"),
        ),
        free_text=True,
        selection_mode="multiple",
        recommended_option_id="one",
        allow_skip=True,
    )
    answers = []

    def ask() -> None:
        with bind(turn_id="turn_0001"):
            answers.append(runtime.prompts.ask(prompt))

    registry = SimpleNamespace(active=runtime)
    app = create_app(registry=registry, boot_token="boot", csrf_token="csrf-test")
    # 不启动 lifespan，避免无关的真实项目注册表与关闭逻辑。
    client = TestClient(app, base_url="http://localhost")
    client.get("/boot?token=boot", follow_redirects=False)
    csrf = "csrf-test"
    worker = threading.Thread(target=ask, daemon=True)

    async def wait_for_prompt() -> None:
        waiter = asyncio.create_task(runtime.events.wait(0, timeout=2))
        await asyncio.sleep(0)
        worker.start()
        await asyncio.wait_for(waiter, timeout=1)

    try:
        asyncio.run(wait_for_prompt())
        assert worker.is_alive(), "事件必须在工具完成之前发出"
        resync, events = runtime.events.after(0)
        assert not resync
        assert events[0].kind == "prompt_requested"
        assert events[0].data["turn_id"] == "turn_0001"
        assert events[0].data["payload"] == {"prompt_id": "question-1"}
        # 首次连接或重连都能取到同一个 pending 快照。
        pending = client.get("/api/v1/prompts").json()["items"]
        assert pending == [prompt.to_payload()]
        headers = {"x-csrf-token": csrf}
        invalid = client.post(
            "/api/v1/prompts/question-1/resolve",
            headers=headers,
            json={"selected_values": ["missing"]},
        )
        assert invalid.status_code == 409
        assert worker.is_alive()
        body = (
            {"skipped": True}
            if skipped
            else {"selected_values": ["one", "two"], "text": "先验证"}
        )
        response = client.post(
            "/api/v1/prompts/question-1/resolve", headers=headers, json=body
        )
        assert response.status_code == 200
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert answers[0].skipped is skipped
        assert client.get("/api/v1/prompts").json()["items"] == []
        assert runtime.events.after(events[0].cursor)[1][0].kind == "prompt_resolved"
        audit = runtime.session.record_tool_event.call_args.args[1]
        assert audit["status"] == ("skipped" if skipped else "answered")
        assert audit["selected_values"] == ([] if skipped else ["one", "two"])
        assert (
            client.post(
                "/api/v1/prompts/question-1/resolve", headers=headers, json=body
            ).status_code
            == 409
        )
    finally:
        runtime.prompts.close()
        worker.join(timeout=2)
        client.close()
