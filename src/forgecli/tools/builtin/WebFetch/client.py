"""直接抓取公开网页: 固定已验证 IP、逐跳校验、限量读取与取消."""

from __future__ import annotations

import asyncio
import re
import zlib
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from forgecli.common import __version__
from forgecli.common.cancellation import CancelledError, CancelToken
from forgecli.tools.builtin.WebFetch.domain_check import (
    normalize_url,
    resolve_and_validate,
)
from forgecli.tools.builtin.WebFetch.markdown import html_to_markdown

MAX_RESPONSE_BYTES = 2_000_000
TIMEOUT_SECONDS = 25
MAX_REDIRECTS = 5
_REDIRECTS = {301, 302, 303, 307, 308}
_TEXT_TYPES = {"text/plain", "text/markdown", "text/x-markdown"}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}


@dataclass(frozen=True)
class WebFetchOutput:
    url: str
    markdown: str
    title: str = ""


async def _read_body(response: httpx.Response) -> bytes:
    """同时限制传输量与解压后的大小, 避免压缩响应突破内存上限."""
    encoding = response.headers.get("content-encoding", "identity").lower().strip()
    if encoding not in ("identity", "gzip", "deflate"):
        raise ValueError("Unsupported WebFetch content encoding")
    decoder = (
        None
        if encoding == "identity"
        else zlib.decompressobj(
            16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        )
    )
    body = bytearray()
    downloaded = 0
    async for chunk in response.aiter_raw():
        downloaded += len(chunk)
        if downloaded > MAX_RESPONSE_BYTES:
            raise ValueError("WebFetch response exceeded byte limit")
        decoded = (
            chunk
            if decoder is None
            else decoder.decompress(chunk, MAX_RESPONSE_BYTES - len(body) + 1)
        )
        body.extend(decoded)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("WebFetch response exceeded decoded byte limit")
    if decoder is not None and (not decoder.eof or decoder.unused_data):
        raise ValueError("Invalid or concatenated compressed WebFetch response")
    return bytes(body)


async def _fetch_http_response(
    url: str, transport: httpx.AsyncBaseTransport | None
) -> tuple[str, bytes, str, str | None]:
    """校验目标与每次重定向, 返回最终 URL、响应体、媒体类型和字符集."""
    current = normalize_url(url)
    visited: set[str] = set()
    for _ in range(MAX_REDIRECTS + 1):
        if current in visited:
            raise ValueError("WebFetch redirect loop")
        visited.add(current)
        parsed = urlsplit(current)
        host = parsed.hostname or ""
        addresses = await resolve_and_validate(
            host, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
        # 不再让 HTTP 库解析域名, 防止校验与连接之间发生 DNS rebinding.
        # Host 和 TLS SNI/证书校验仍使用原始域名. 每跳独立连接, 不跨主机复用 TLS.
        address = sorted(addresses, key=lambda ip: (":" in ip, ip))[0]
        pinned_url = httpx.URL(current).copy_with(host=address)
        async with (
            httpx.AsyncClient(
                transport=transport, trust_env=False, follow_redirects=False
            ) as http,
            http.stream(
                "GET",
                pinned_url,
                headers={
                    "Host": parsed.netloc,
                    "User-Agent": f"forge/{__version__}",
                    "Accept": (
                        "text/html,application/xhtml+xml,text/plain,text/markdown"
                    ),
                    "Accept-Encoding": "gzip, deflate",
                },
                extensions={"sni_hostname": host},
                timeout=TIMEOUT_SECONDS,
            ) as response,
        ):
            if response.status_code in _REDIRECTS:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("WebFetch redirect missing Location")
                # 禁止 HTTPS 降级以及所有不符合域名/IP/凭证规则的重定向.
                target = normalize_url(urljoin(current, location))
                if parsed.scheme == "https" and urlsplit(target).scheme != "https":
                    raise ValueError("WebFetch HTTPS downgrade is forbidden")
                current = target
                continue
            response.raise_for_status()
            media_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if media_type not in _TEXT_TYPES | _HTML_TYPES:
                raise ValueError("WebFetch supports HTML, Markdown and plain text only")
            if "attachment" in response.headers.get("content-disposition", "").lower():
                raise ValueError("WebFetch does not download attachments")
            body = await _read_body(response)
            return current, body, media_type, response.charset_encoding
    raise ValueError("WebFetch exceeded redirect limit")


async def _wait_for_abort(abort: CancelToken) -> None:
    while not abort.cancelled:
        await asyncio.sleep(0.05)


async def fetch_page(
    url: str,
    *,
    abort: CancelToken,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebFetchOutput:
    """直接 GET 网页, 网络总时限包含 DNS 和全部重定向; 完成后在本地转 Markdown."""
    if abort.cancelled:
        raise CancelledError()
    request = asyncio.create_task(_fetch_http_response(url, transport))
    cancelled = asyncio.create_task(_wait_for_abort(abort))
    try:
        done, _ = await asyncio.wait(
            (request, cancelled),
            timeout=TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if abort.cancelled:
            raise CancelledError()
        if request not in done:
            raise TimeoutError("WebFetch request timed out")
        final_url, body, media_type, encoding = request.result()
    except httpx.TimeoutException as exc:
        raise TimeoutError("WebFetch request timed out") from exc
    finally:
        request.cancel()
        cancelled.cancel()
        await asyncio.gather(request, cancelled, return_exceptions=True)
    if media_type in _HTML_TYPES:
        title, markdown = html_to_markdown(body, final_url, encoding=encoding)
    else:
        try:
            markdown = body.decode(encoding or "utf-8")
        except (LookupError, UnicodeDecodeError):
            markdown = body.decode("utf-8", errors="replace")
        title = ""
        if not markdown.strip():
            raise ValueError("WebFetch returned empty text")
        # 纯文本不是 Markdown, 用足够长的围栏保留字面内容.
        if media_type == "text/plain":
            fence = "`" * (
                max((len(run) for run in re.findall(r"`+", markdown)), default=2) + 1
            )
            markdown = f"{fence}text\n{markdown}\n{fence}"
    if abort.cancelled:
        raise CancelledError()
    if not markdown.strip():
        raise ValueError("WebFetch found no readable page content")
    return WebFetchOutput(final_url, markdown.strip(), title)
