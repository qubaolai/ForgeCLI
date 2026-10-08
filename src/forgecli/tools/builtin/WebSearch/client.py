"""Parallel 的无会话 MCP HTTP 调用; 限流读取、总时限与协作取消."""

from __future__ import annotations

import asyncio
import codecs
import json
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from forgecli.common.cancellation import CancelledError, CancelToken

CALL_PARALLEL_URL = "https://search.parallel.ai/mcp"
MAX_RESPONSE_BYTES = 1_000_000
MCP_TIMEOUT_SECONDS = 25
_CANCEL_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class WebSearchOutput:
    text: str
    is_error: bool = False


def parse_payload(text: str) -> WebSearchOutput | None:
    """None 仅表示别的请求或通知; 协议错误绝不当作空搜索结果."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON in WebSearch response") from exc
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        raise ValueError("Invalid WebSearch JSON-RPC response")
    if "id" not in payload and isinstance(payload.get("method"), str):
        return None
    if type(payload.get("id")) is not int or payload["id"] != 1:
        return None
    if "error" in payload:
        error = payload["error"]
        if not isinstance(error, dict):
            raise ValueError("Invalid WebSearch JSON-RPC error")
        return WebSearchOutput(
            f"WebSearch error ({error.get('code')}): {error.get('message')}",
            is_error=True,
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Missing WebSearch result")
    is_error = result.get("isError", False)
    if not isinstance(is_error, bool):
        raise ValueError("Invalid WebSearch isError flag")
    content = result.get("content")
    if not isinstance(content, list):
        raise ValueError("Missing WebSearch content")
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("Invalid WebSearch content block")
        if block.get("type") == "text":
            if not isinstance(block.get("text"), str):
                raise ValueError("Invalid WebSearch text block")
            texts.append(block["text"])
    text = "\n\n".join(texts).strip()
    if not text and isinstance(result.get("structuredContent"), dict):
        text = json.dumps(result["structuredContent"], ensure_ascii=False)
    if not text and content:
        raise ValueError("WebSearch returned no supported text content")
    return WebSearchOutput(
        text or ("WebSearch failed" if is_error else "No search results found."),
        is_error=is_error,
    )


class _SSEParser:
    def __init__(self) -> None:
        self._buffer = ""
        self._data: list[str] = []
        self._pending_cr = False

    def feed(self, text: str, *, final: bool = False) -> WebSearchOutput | None:
        if text:
            # CR 已作为换行处理; 下一个块开头的 LF 属于同一个 CRLF.
            if self._pending_cr:
                text = text.removeprefix("\n")
            self._pending_cr = text.endswith("\r")
            self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition("\n")
            output = self._line(line)
            if output is not None:
                return output
        if final:
            if self._buffer:
                self._line(self._buffer)
                self._buffer = ""
            return self._line("")
        return None

    def _line(self, line: str) -> WebSearchOutput | None:
        if not line:
            if not self._data:
                return None
            data = "\n".join(self._data)
            self._data.clear()
            return parse_payload(data)
        field, separator, value = line.partition(":")
        if field == "data":
            self._data.append(value.removeprefix(" ") if separator else "")
        return None


async def _request(
    value: Mapping[str, object],
    headers: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None,
) -> WebSearchOutput:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "web_search", "arguments": dict(value)},
    }
    request_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **headers,
    }
    async with (
        httpx.AsyncClient(transport=transport) as http,
        http.stream(
            "POST",
            CALL_PARALLEL_URL,
            json=payload,
            headers=request_headers,
            timeout=MCP_TIMEOUT_SECONDS,
        ) as response,
    ):
        response.raise_for_status()
        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        if media_type not in ("application/json", "text/event-stream"):
            raise ValueError("Unsupported WebSearch response Content-Type")
        parser = _SSEParser() if media_type == "text/event-stream" else None
        decoder = codecs.getincrementaldecoder("utf-8")()
        chunks: list[str] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"WebSearch response exceeded {MAX_RESPONSE_BYTES} bytes"
                )
            text = decoder.decode(chunk)
            if parser is not None:
                output = parser.feed(text)
                if output is not None:
                    return output
            else:
                chunks.append(text)
        tail = decoder.decode(b"", final=True)
        if parser is not None:
            output = parser.feed(tail, final=True)
        else:
            output = parse_payload("".join(chunks) + tail)
        if output is None:
            raise ValueError("WebSearch response has no matching result")
        return output


async def _wait_for_abort(abort: CancelToken) -> None:
    while not abort.cancelled:
        await asyncio.sleep(_CANCEL_POLL_SECONDS)


async def call_mcp(
    value: Mapping[str, object],
    *,
    headers: Mapping[str, str],
    abort: CancelToken,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebSearchOutput:
    """竞速取消与整个请求, 包括等待响应头和读取阻塞; 退出前关闭连接."""
    if abort.cancelled:
        raise CancelledError()
    request = asyncio.create_task(_request(value, headers, transport))
    cancelled = asyncio.create_task(_wait_for_abort(abort))
    try:
        done, _ = await asyncio.wait(
            (request, cancelled),
            timeout=MCP_TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        # 再次检查取消: 防止请求完成同时也进行取消, 取消优先于请求完成, 语义上更安全
        if abort.cancelled:
            raise CancelledError()
        if request not in done:
            raise TimeoutError("WebSearch request timed out")
        return request.result()
    except httpx.TimeoutException as exc:
        raise TimeoutError("WebSearch request timed out") from exc
    finally:
        request.cancel()
        cancelled.cancel()
        await asyncio.gather(request, cancelled, return_exceptions=True)
