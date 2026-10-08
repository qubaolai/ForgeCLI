import asyncio
import gzip
import threading
from unittest.mock import Mock

import httpx
import pytest

from forgecli.common.cancellation import CancelledError, CancelToken
from forgecli.core.agent.sub_agent import SubAgentIdentity
from forgecli.core.permissions import (
    DecisionReasonType,
    PermissionDecisionReason,
    allow,
    deny,
)
from forgecli.core.tool.tool_call import ToolCall
from forgecli.tools.builtin.WebFetch import client, domain_check
from forgecli.tools.builtin.WebFetch import tool as tool_module
from forgecli.tools.builtin.WebFetch.client import WebFetchOutput, fetch_page
from forgecli.tools.builtin.WebFetch.markdown import html_to_markdown
from forgecli.tools.builtin.WebFetch.tool import WebFetch
from forgecli.tools.execution import run_tool_use
from forgecli.tools.runner import sub_agent_runner
from test_web_search import stack as _stack

stack = _stack  # 共用隔离真实系统配置的组合根 fixture


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, stall=False):
        self.chunks = chunks
        self.stall = stall
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk
        if self.stall:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def response(
    body=b"<main><h1>Hello</h1><p>world</p></main>", *, status=200, headers=None
):
    return httpx.Response(
        status,
        headers={"Content-Type": "text/html", **(headers or {})},
        stream=Stream([body]),
    )


@pytest.fixture
def public_dns(monkeypatch):
    calls = []

    async def resolve(host, port):
        calls.append((host, port))
        return {"93.184.216.34"}

    monkeypatch.setattr(client, "resolve_and_validate", resolve)
    return calls


def test_body_to_markdown_preserves_document_structure():
    html = b"""<html><head><title>Guide</title><script>evil</script></head><body>
    <nav>navigation junk</nav><main><h1>Install</h1>
    <p>Intro <a href="../api">API</a></p>
    <div class="advertisement">ad junk</div><div hidden>hidden junk</div>
    <script>run junk</script><ul><li>First</li><li>Second</li></ul>
    <pre><code class="language-python">if True:\n    print("yes")\n</code></pre>
    <table><tr><th>Name</th><th>Value</th></tr><tr><td>Timeout</td><td>25</td></tr></table>
    <img src="https://evil.example/tracker" alt="diagram">
    <a href="javascript:alert(1)">bad link</a></main>
    <footer>footer junk</footer></body></html>"""
    title, md = html_to_markdown(html, "https://example.com/docs/page")
    assert title == "Guide"
    assert "# Install" in md and "[API](https://example.com/api)" in md
    assert "- First" in md and "```python" in md and '    print("yes")' in md
    assert "| Name | Value |" in md and "| Timeout | 25 |" in md
    assert "diagram" in md and "bad link" in md
    for noise in ("junk", "javascript:", "evil.example", "![]", "<script>"):
        assert noise not in md


def test_html_encoding_and_multiple_articles():
    body = (
        '<meta charset="gb18030"><body><article>第一篇</article>'
        "<article>第二篇</article></body>"
    ).encode("gb18030")
    assert "第一篇" in html_to_markdown(body, "https://example.com")[1]
    assert "第二篇" in html_to_markdown(body, "https://example.com")[1]


def test_pins_ip_preserves_host_sni_and_ignores_proxy(public_dns, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1234")
    requests = []

    def handler(req):
        requests.append(req)
        assert req.url.host == "93.184.216.34"
        assert req.headers["host"] == "example.com"
        assert req.extensions["sni_hostname"] == "example.com"
        assert "authorization" not in req.headers and "cookie" not in req.headers
        return response()

    output = asyncio.run(
        fetch_page(
            "https://example.com/page",
            abort=CancelToken(),
            transport=httpx.MockTransport(handler),
        )
    )
    assert output.url == "https://example.com/page" and "# Hello" in output.markdown
    assert public_dns == [("example.com", 443)] and len(requests) == 1


def test_redirect_resolves_again_and_does_not_forward_cookies(public_dns):
    requests = []

    def handler(req):
        requests.append(req)
        if len(requests) == 1:
            return response(
                status=302,
                headers={
                    "Location": "https://other.example/docs",
                    "Set-Cookie": "secret=value",
                },
            )
        assert req.headers["host"] == "other.example"
        assert req.extensions["sni_hostname"] == "other.example"
        assert "cookie" not in req.headers
        return response()

    out = asyncio.run(
        fetch_page(
            "https://example.com",
            abort=CancelToken(),
            transport=httpx.MockTransport(handler),
        )
    )
    assert out.url == "https://other.example/docs"
    assert public_dns == [("example.com", 443), ("other.example", 443)]


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/",
        "https://box.local/",
        "file:///etc/passwd",
        "http://example.com/",
        "https://user:pass@example.com/",
    ],
)
def test_rejects_unsafe_redirect_before_next_connection(public_dns, location):
    handler = Mock(return_value=response(status=302, headers={"Location": location}))
    with pytest.raises(ValueError):
        asyncio.run(
            fetch_page(
                "https://example.com",
                abort=CancelToken(),
                transport=httpx.MockTransport(handler),
            )
        )
    assert handler.call_count == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/pdf"},
        {"Content-Disposition": "attachment; filename=a.html"},
    ],
)
def test_unsupported_content(public_dns, headers):
    with pytest.raises(ValueError):
        asyncio.run(
            fetch_page(
                "https://example.com",
                abort=CancelToken(),
                transport=httpx.MockTransport(lambda r: response(headers=headers)),
            )
        )


def test_gzip_limit_before_large_allocation(public_dns, monkeypatch):
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 1000)
    stream = Stream([gzip.compress(b"x" * 100_000), b"unread"])
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            headers={"Content-Type": "text/html", "Content-Encoding": "gzip"},
            stream=stream,
        )
    )
    with pytest.raises(ValueError, match="decoded byte limit"):
        asyncio.run(
            fetch_page("https://example.com", abort=CancelToken(), transport=transport)
        )
    assert stream.closed and stream.read == 1


def test_cancel_blocked_read(public_dns):
    stream = Stream([], stall=True)
    abort = CancelToken()
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, headers={"Content-Type": "text/html"}, stream=stream
        )
    )

    async def run():
        asyncio.get_running_loop().call_later(0.01, abort.cancel)
        return await asyncio.wait_for(
            fetch_page("https://example.com", abort=abort, transport=transport), 1
        )

    with pytest.raises(CancelledError):
        asyncio.run(run())
    assert stream.closed


def test_dns_timeout_does_not_wait_for_system_resolver(monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_dns(*a, **kw):
        started.set()
        release.wait(2)
        return []

    monkeypatch.setattr(domain_check.socket, "getaddrinfo", slow_dns)
    monkeypatch.setattr(client, "TIMEOUT_SECONDS", 0.03)
    try:
        with pytest.raises(TimeoutError):
            asyncio.run(fetch_page("https://example.com", abort=CancelToken()))
        assert started.is_set() and not release.is_set()
    finally:
        release.set()


def test_network_deadline_and_plain_text(public_dns, monkeypatch):
    monkeypatch.setattr(client, "TIMEOUT_SECONDS", 0.02)

    async def stall(req):
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        asyncio.run(
            fetch_page(
                "https://example.com",
                abort=CancelToken(),
                transport=httpx.MockTransport(stall),
            )
        )
    out = asyncio.run(
        fetch_page(
            "https://example.com",
            abort=CancelToken(),
            transport=httpx.MockTransport(
                lambda r: response(b"a\n```\nb", headers={"Content-Type": "text/plain"})
            ),
        )
    )
    assert out.markdown.startswith("````text")


def test_registered_readonly_persistence_and_permission(stack, monkeypatch, tmp_path):
    assert "WebFetch" in [schema.name for schema in stack.runner.tool_schemas()]
    child = sub_agent_runner(stack.runner, SubAgentIdentity("s", "t", "a", "c"), Mock())
    context = child.context(
        session_id="s", turn_id="t", messages=(), cancel=CancelToken()
    )
    calls = []

    async def fake_fetch(url, *, abort):
        calls.append(url)
        return WebFetchOutput(url, "# Heading\n" + "x" * 50_000, "Title")

    monkeypatch.setattr(tool_module, "fetch_page", fake_fetch)
    invocation = ToolCall("fetch-1", "web_fetch", {"url": "https://example.com/"})
    outcome = run_tool_use(invocation, context, lambda *a: allow(), None)
    assert outcome.status == "ok" and "<persisted-output>" in outcome.block.content
    assert (
        "Source: https://example.com/"
        in next(tmp_path.rglob("fetch-1.txt")).read_text()
    )
    denied = run_tool_use(
        invocation,
        context,
        lambda *a: deny("blocked", PermissionDecisionReason(DecisionReasonType.OTHER)),
        None,
    )
    assert not denied.executed and len(calls) == 1
    assert WebFetch().is_search_or_read_command({}).is_read


def test_invalid_input_performs_no_dns(monkeypatch):
    resolver = Mock(side_effect=AssertionError("must not resolve before authorization"))
    monkeypatch.setattr(domain_check.socket, "getaddrinfo", resolver)
    assert WebFetch().validate_input({"url": "https://example.com"}, Mock()).ok
    assert not WebFetch().validate_input({"url": "http://127.0.0.1"}, Mock()).ok
    resolver.assert_not_called()


def test_embedded_markdown_code_fences_are_preserved():
    _, md = html_to_markdown(
        b"<main><pre>```python\nprint(1)\n```</pre></main>", "https://example.com"
    )
    assert md.startswith("````\n```python") and md.endswith("\n````")


def test_size_limit_closes_stream_early(public_dns, monkeypatch):
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 10)
    stream = Stream([b"x" * 6, b"x" * 6, b"unread"])
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, headers={"Content-Type": "text/html"}, stream=stream
        )
    )
    with pytest.raises(ValueError, match="byte limit"):
        asyncio.run(
            fetch_page("https://example.com", abort=CancelToken(), transport=transport)
        )
    assert stream.closed and stream.read == 2


def test_valid_gzip_and_redirect_loop(public_dns):
    transport = httpx.MockTransport(
        lambda r: response(
            gzip.compress(b"<main><h1>Compressed</h1></main>"),
            headers={"Content-Encoding": "gzip"},
        )
    )
    out = asyncio.run(
        fetch_page("https://example.com", abort=CancelToken(), transport=transport)
    )
    assert "# Compressed" in out.markdown
    loop = httpx.MockTransport(
        lambda r: response(status=302, headers={"Location": "/"})
    )
    with pytest.raises(ValueError, match="redirect loop"):
        asyncio.run(
            fetch_page("https://example.com", abort=CancelToken(), transport=loop)
        )
