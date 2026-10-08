import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import jsonschema
import pytest

from forgecli.agent.run_bus import AgentRunEventBus
from forgecli.common.cancellation import CancelledError, CancelToken
from forgecli.core.agent.sub_agent import SubAgentIdentity
from forgecli.core.permissions import (
    DecisionReasonType,
    PermissionDecisionReason,
    ToolPermissionContext,
    allow,
    deny,
)
from forgecli.core.tool.tool_call import ToolCall
from forgecli.runtime import tool_wiring
from forgecli.tools.builtin.WebSearch import client
from forgecli.tools.builtin.WebSearch import tool as tool_module
from forgecli.tools.builtin.WebSearch.client import WebSearchOutput, call_mcp
from forgecli.tools.builtin.WebSearch.tool import WebSearch
from forgecli.tools.execution import run_tool_use
from forgecli.tools.runner import sub_agent_runner


def result(text="标题 https://example.com 摘录", **extra):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": text}], **extra},
    }


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, stall=False, delay=0):
        self.chunks = chunks
        self.stall = stall
        self.delay = delay
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.read += 1
            yield chunk
        if self.stall:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def request(stream, *, media_type="application/json", abort=None):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, headers={"Content-Type": media_type}, stream=stream
        )
    )
    return call_mcp(
        {"objective": "test", "search_queries": ["test"]},
        headers={},
        abort=abort or CancelToken(),
        transport=transport,
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_sse_notifications_multiline_utf8_and_early_close(newline):
    notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"})
    other = json.dumps({**result("wrong request"), "id": 2})
    payload = json.dumps(result(), ensure_ascii=False, indent=2)
    body = newline.join(
        [
            ": keepalive",
            "event: message",
            f"data:{notification}",
            "",
            f"data: {other}",
            "",
        ]
        + [f"data:{line}" for line in payload.splitlines()]
        + ["", ""]
    ).encode()
    # 单字节分块覆盖 UTF-8、CRLF 和 SSE 字段横跨网络块.
    stream = Stream([body[i : i + 1] for i in range(len(body))], stall=True)
    output = asyncio.run(
        asyncio.wait_for(request(stream, media_type="text/event-stream"), 1)
    )
    assert output == WebSearchOutput("标题 https://example.com 摘录")
    assert stream.closed


def test_json_result_preserves_sources():
    stream = Stream([json.dumps(result()).encode()])
    assert asyncio.run(request(stream)).text == "标题 https://example.com 摘录"
    assert stream.closed


@pytest.mark.parametrize(
    "payload",
    [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32602, "message": "bad arguments"},
        },
        result("rate limited", isError=True),
    ],
)
def test_rpc_and_tool_errors_map_to_error_blocks(payload):
    output = asyncio.run(request(Stream([json.dumps(payload).encode()])))
    block = WebSearch().map_tool_result_to_block(output, "call-1")
    assert block.is_error
    assert block.tool_call_id == "call-1"
    assert "bad arguments" in block.content or "rate limited" in block.content


@pytest.mark.parametrize(
    "body",
    [
        b"<html>bad gateway</html>",
        b"null",
        b"{}",
        b"",
        b'{"jsonrpc":"2.0","id":1}',
        json.dumps({**result(), "id": 2}).encode(),
        json.dumps(result(isError="false")).encode(),
    ],
)
def test_invalid_response_is_not_success(body):
    stream = Stream([body])
    with pytest.raises(ValueError):
        asyncio.run(request(stream))
    assert stream.closed


def test_empty_and_structured_results():
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}
    assert "No search results" in client.parse_payload(json.dumps(payload)).text
    payload["result"]["structuredContent"] = {
        "results": [{"url": "https://example.com"}]
    }
    assert "https://example.com" in client.parse_payload(json.dumps(payload)).text


def test_size_limit_stops_before_consuming_body(monkeypatch):
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 10)
    stream = Stream([b" " * 6, b" " * 6, b"never read"])
    with pytest.raises(ValueError, match="exceeded"):
        asyncio.run(request(stream))
    assert stream.read == 2
    assert stream.closed


@pytest.mark.parametrize("stall", [True, False])
def test_total_timeout_covers_stall_and_continuous_data(monkeypatch, stall):
    monkeypatch.setattr(client, "MCP_TIMEOUT_SECONDS", 0.04)
    stream = Stream([] if stall else [b" "] * 100, stall=stall, delay=0.005)
    with pytest.raises(TimeoutError, match="timed out"):
        asyncio.run(asyncio.wait_for(request(stream), 1))
    assert stream.closed
    assert stream.read < 100


def test_cancel_interrupts_blocked_read():
    abort = CancelToken()
    stream = Stream([], stall=True)

    async def run():
        asyncio.get_running_loop().call_later(0.01, abort.cancel)
        return await asyncio.wait_for(request(stream, abort=abort), 1)

    with pytest.raises(CancelledError):
        asyncio.run(run())
    assert stream.closed


@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_and_deadline_while_waiting_for_headers(monkeypatch, cancel):
    monkeypatch.setattr(client, "MCP_TIMEOUT_SECONDS", 0.1)
    abort = CancelToken()
    cleaned_up = []

    async def handler(req):
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.append(True)

    async def run():
        if cancel:
            asyncio.get_running_loop().call_later(0.01, abort.cancel)
        return await call_mcp(
            {}, headers={}, abort=abort, transport=httpx.MockTransport(handler)
        )

    with pytest.raises(CancelledError if cancel else TimeoutError):
        asyncio.run(asyncio.wait_for(run(), 1))
    assert cleaned_up


def test_precancelled_makes_no_request():
    abort = CancelToken()
    abort.cancel()
    handler = Mock()
    with pytest.raises(CancelledError):
        asyncio.run(
            call_mcp(
                {}, headers={}, abort=abort, transport=httpx.MockTransport(handler)
            )
        )
    handler.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_http_failure_is_not_success(status):
    transport = httpx.MockTransport(lambda req: httpx.Response(status))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(call_mcp({}, headers={}, abort=CancelToken(), transport=transport))


@pytest.mark.parametrize("count", [0, 11, -1, True, "10", 1.5])
def test_invalid_counts_rejected(count):
    tool = WebSearch(api_key="test-key")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"query": "test", "num_results": count}, tool.input_schema)
    assert not tool.validate_input({"query": "test", "num_results": count}, Mock()).ok


@pytest.mark.parametrize(
    "api_key, count", [("", None), ("test-key", None), ("test-key", 10)]
)
def test_request_configuration(monkeypatch, api_key, count):
    captured = {}

    async def fake_call(value, *, headers, abort):
        captured.update(value=value, headers=headers)
        return WebSearchOutput("ok")

    monkeypatch.setattr(tool_module, "call_mcp", fake_call)
    tool = WebSearch(api_key=api_key)
    args = {"query": "  search terms  "}
    if count is not None:
        args["num_results"] = count
    assert (
        tool.call(args, SimpleNamespace(abort=CancelToken()), Mock(), None).data.text
        == "ok"
    )
    assert captured["value"] == {
        "objective": "search terms",
        "search_queries": ["search terms"],
    }
    if api_key:
        assert captured["headers"]["Authorization"] == "Bearer test-key"
        config = json.loads(captured["headers"]["x-parallel-search-config"])
        assert config == {
            "mode": "fast",
            "advanced_settings": {"max_results": count or 5},
        }
    else:
        assert "Authorization" not in captured["headers"]
        assert "x-parallel-search-config" not in captured["headers"]
        assert "num_results" not in tool.input_schema["properties"]
    assert not tool.validate_input({"query": "   "}, Mock()).ok
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"query": "test", "contextMaxCharacters": 100}, tool.input_schema
        )


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "forgecli.settings.paths.runtime_dir", lambda: tmp_path / "runtime"
    )
    monkeypatch.setattr("forgecli.runtime.sandbox_wiring.runtime_dir", lambda: tmp_path)
    # 组合根真的构造工具列表; 仅隔离系统沙箱探测、用户配置与外部服务.
    monkeypatch.setattr(tool_wiring, "protected_read_paths", lambda: ())
    monkeypatch.setattr(
        tool_wiring, "probe_execution_profile", lambda **kwargs: Profile()
    )
    monkeypatch.setattr(
        tool_wiring,
        "build_execution_environment",
        lambda p: {"PARALLEL_API_KEY": "test-key"},
    )
    monkeypatch.setattr(
        tool_wiring,
        "select_provider",
        lambda: (Mock(), SimpleNamespace(confined=False)),
    )
    monkeypatch.setattr(tool_wiring, "projects_dir", lambda: tmp_path)
    monkeypatch.setattr(tool_wiring, "read_sandbox", lambda p: None)
    for name in (
        "project_settings_file",
        "user_settings_file",
        "project_memory_file",
        "user_memory_file",
    ):
        monkeypatch.setattr(tool_wiring, name, lambda *a: tmp_path / "unused.json")
    session = Mock()
    session.current.return_value.session_id = "session"
    return tool_wiring.build_tool_stack(
        workspace_roots=(str(tmp_path),),
        workspace_id="workspace",
        session=session,
        permission=ToolPermissionContext(),
        run_bus=AgentRunEventBus(),
    )


# 真实组合根只访问画像的这两个字段, dataclass 支持 replace.


@dataclass
class Profile:
    isolation_level: object = None
    shell_launch: object = None


def test_registered_readonly_child_error_and_persistence(stack, monkeypatch, tmp_path):
    assert "WebSearch" in [schema.name for schema in stack.runner.tool_schemas()]
    child = sub_agent_runner(
        stack.runner, SubAgentIdentity("session", "turn", "child", "call"), Mock()
    )
    child.can_use_tool = lambda *a: allow()
    context = child.context(
        session_id="session", turn_id="turn", messages=(), cancel=CancelToken()
    )
    search = next(t for t in child.pool() if t.name == "WebSearch")
    assert "num_results" in search.input_schema["properties"]

    async def fake_call(*a, **kw):
        return WebSearchOutput("https://example.com\n" + "x" * 50_000)

    monkeypatch.setattr(tool_module, "call_mcp", fake_call)
    outcome = run_tool_use(
        ToolCall("search-1", "web_search", {"query": "test"}),
        context,
        child.can_use_tool,
        None,
    )
    assert outcome.status == "ok"
    assert "<persisted-output>" in outcome.block.content
    assert list(tmp_path.rglob("search-1.txt"))

    async def failed(*a, **kw):
        return WebSearchOutput("rate limited", is_error=True)

    monkeypatch.setattr(tool_module, "call_mcp", failed)
    outcome = run_tool_use(
        ToolCall("search-2", "WebSearch", {"query": "test"}),
        context,
        child.can_use_tool,
        None,
    )
    assert outcome.status == "error"
    assert outcome.block.is_error

    # 显式权限拒绝先于网络调用.

    outcome = run_tool_use(
        ToolCall("search-3", "WebSearch", {"query": "test"}),
        context,
        lambda *a: deny("blocked", PermissionDecisionReason(DecisionReasonType.OTHER)),
        None,
    )
    assert not outcome.executed
