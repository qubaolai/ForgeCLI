"""公开网页 URL 与公网地址校验; 抓取端只连接返回的已验证地址."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import threading
from contextlib import suppress
from urllib.parse import parse_qsl, urlsplit, urlunsplit

# 可信代码配置; *.example.com 仅匹配子域名, example.com 匹配域名本身.
BLOCK_DOMAIN: tuple[str, ...] = ()
_DNS_SLOTS = threading.BoundedSemaphore(16)
_LOCAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".invalid",
    ".test",
)
_SECRET_PARAMETERS = {
    "token",
    "access_token",
    "api_key",
    "apikey",
    "key",
    "secret",
    "password",
    "authorization",
    "auth",
    "signature",
    "sig",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "x-goog-signature",
    "x-goog-credential",
}


def normalize_host(host: str) -> str:
    """统一大小写、尾点与国际化域名; 不接受 IPv6 zone 或编码后的主机名."""
    host = host.rstrip(".").lower()
    if not host or "%" in host:
        raise ValueError("Invalid hostname")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid hostname") from exc


def domain_matches(host: str, pattern: str) -> bool:
    """按 DNS 标签边界匹配域名, 避免 example.com 匹配到 notexample.com."""
    host = normalize_host(host)
    if pattern.startswith("*."):
        base = normalize_host(pattern[2:])
        return host != base and host.endswith("." + base)
    return host == normalize_host(pattern)


def validate_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """仅允许全球单播地址, 同时检查 IPv4-mapped IPv6 中嵌入的地址."""
    ip = ipaddress.ip_address(value)
    if not ip.is_global or ip.is_multicast:
        raise ValueError("Non-public addresses are forbidden")
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            validate_ip(str(ip.ipv4_mapped))
        # 禁止转换/隧道地址, 不把其中嵌入的地址当成独立公网目标.
        if ip.sixtofour is not None or ip.teredo is not None:
            raise ValueError("IPv6 tunnel addresses are forbidden")
        if ip in ipaddress.ip_network("64:ff9b::/96"):
            raise ValueError("IPv6 translation addresses are forbidden")
    return ip


def normalize_url(url: str) -> str:
    """不联网地校验并规范 URL; 可在权限裁决之前调用."""
    url = url.strip()
    if (
        not url
        or len(url) > 8192
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
    ):
        raise ValueError("Invalid URL length or whitespace")
    if "\\" in url:
        raise ValueError("Backslashes in URLs are forbidden")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid URL") from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Only HTTP and HTTPS URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are forbidden")
    host = normalize_host(parsed.hostname or "")
    if port is not None and port not in (80, 443):
        raise ValueError("Only public web ports 80 and 443 are supported")
    for pattern in BLOCK_DOMAIN:
        if pattern and domain_matches(host, pattern):
            raise ValueError("Hostname is blocked by policy")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # 127.1、十进制/八进制/十六进制 IPv4 在某些解析器里也是 IP.
        try:
            legacy_ip = socket.inet_aton(host)
        except OSError:
            ip = None
        else:
            ip = ipaddress.ip_address(legacy_ip)
    if ip is not None:
        validate_ip(str(ip))
        host = str(ip)
    else:
        if "." not in host or host.endswith(_LOCAL_SUFFIXES):
            raise ValueError("Local hostnames are forbidden")
        if len(host) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError("Invalid hostname")
    if any(key.lower() in _SECRET_PARAMETERS for key, _ in parse_qsl(parsed.query)):
        raise ValueError("URLs containing credential parameters are forbidden")
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    # fragment 不参与服务端取页, 不发送给第三方.
    return urlunsplit(
        (parsed.scheme.lower(), authority, parsed.path or "/", parsed.query, "")
    )


async def resolve_and_validate(hostname: str, port: int) -> set[str]:
    """拒绝解析到任一非公网地址的主机; 抓取端须固定连接到返回的地址."""
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None:
        return {str(validate_ip(str(ip)))}
    # 系统 getaddrinfo 不能取消. 使用有数量上限的后台线程, 避免 asyncio.run
    # 在关闭默认 executor 时等到 DNS 结束, 破坏调用的取消与总时限.
    loop = asyncio.get_running_loop()
    resolved: asyncio.Future[set[str]] = loop.create_future()
    if not _DNS_SLOTS.acquire(blocking=False):
        raise ValueError("Too many pending DNS checks; retry later")

    def finish(addresses: set[str], error: Exception | None) -> None:
        if resolved.done():
            return
        if error is not None:
            resolved.set_exception(error)
        else:
            resolved.set_result(addresses)

    def resolve() -> None:
        addresses: set[str] = set()
        error: Exception | None = None
        try:
            infos = socket.getaddrinfo(
                hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            )
            addresses = {str(item[4][0]) for item in infos}
        except (OSError, ValueError) as exc:
            error = exc
        finally:
            _DNS_SLOTS.release()
        # 请求可能已取消, 其事件循环已关闭.
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(finish, addresses, error)

    worker = threading.Thread(target=resolve, name="webfetch-dns", daemon=True)
    try:
        worker.start()
    except RuntimeError:
        _DNS_SLOTS.release()
        raise
    addresses = await resolved
    if not addresses:
        raise ValueError("DNS resolution returned no address")
    for address in addresses:
        validate_ip(address)
    return addresses


async def validate_url(url: str) -> None:
    """在实际调用阶段检查公网 DNS; 不下载页面, 不接触用户目标服务."""
    parsed = urlsplit(normalize_url(url))
    await resolve_and_validate(
        parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)
    )
