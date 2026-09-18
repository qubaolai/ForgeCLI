"""本机代理: 围栏内的网络只能到它这里, 它按域名放行 (ADR-0055 决策 7; 对应
sandbox-runtime 的 http-proxy / socks-proxy / mux-proxy 与 sandbox-manager 的 host
裁决).

围栏把子进程的网络全拦掉, 只留一条到本机 `127.0.0.1:<port>` 的路 (Linux 是一个 bind 进去
的 unix socket); 子进程的 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 指向它. 一个监听端口同时
接 HTTP 代理 (绝对 URI 转发与 CONNECT 隧道) 和 SOCKS5 (第一个字节是 0x05 就是 SOCKS),
curl / pip / npm / git-https 走前者, ssh / psql 一类走后者.

**裁决在这里, 不在内核**: 内核只保证"除了代理谁都连不上", 域名对不对是代理说了算. 顺序
与 sandbox-runtime 同: 先 deniedDomains, 再 allowedDomains, 都没命中就问人 (有人在场时),
问不了就拒. 问过的答案本会话记住 (按 host), 同一 host 的并发请求合成一次提问.

只裁 host, 不看端口与路径: `*.example.com` 放的是整个子域, 与 Claude Code 同.
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
import select
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from forgecli.common.observability.log import get_log

__all__ = ["DomainPolicy", "NetworkProxy", "matches_domain_pattern"]

_log = get_log(__name__)

_HOST_CHARS = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_HEADER_BYTES = 64 * 1024
_UPSTREAM_TIMEOUT = 30.0
_IDLE_TIMEOUT = 600.0
_SOCKS_VERSION = 0x05


def matches_domain_pattern(host: str, pattern: str) -> bool:
    """`*` 全部; `*.example.com` 严格子域 (不含 example.com 自己); 其余精确比, 不分大小
    写.
    IP 字面量不参与子域匹配: `::ffff:1.2.3.4%x.allowed.com` 这种带 zone 的写法能骗过
    endsWith, 而系统实际连的是那个 IP."""
    lowered = host.lower()
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        if _is_ip(lowered):
            return False
        return lowered.endswith("." + pattern[2:].lower())
    return lowered == pattern.lower()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _canonical_host(host: str) -> str | None:
    """规范化成 getaddrinfo 真正会解析的样子: 去括号, 小写, 去尾点; IPv4 的简写
    (`127.1`, `2130706433`) 与 IPv6 的压缩形态归一. 不合法返回 None."""
    bare = host.strip("[]").rstrip(".").lower()
    if not bare or len(bare) > 255 or "%" in bare:
        return None
    try:
        return str(ipaddress.ip_address(bare))
    except ValueError:
        pass
    try:
        # inet_aton 接受 `127.1` / 纯数字 / 十六进制八位组, 与 libc 一致.
        return socket.inet_ntoa(socket.inet_aton(bare))
    except OSError:
        pass
    if _HOST_CHARS.match(bare):
        return bare
    return None


AskHost = Callable[[str, int], bool]


@dataclass
class DomainPolicy:
    """host 裁决. `ask` 为 None 表示没人在场 (非交互), 不认识的一律拒."""

    allowed: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    ask: AskHost | None = None
    _session_answers: dict[str, bool] = field(default_factory=dict)
    _asking: dict[str, threading.Lock] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, allowed: tuple[str, ...], denied: tuple[str, ...]) -> None:
        """设置变了 (用户点了"以后不用再问"): 换掉两张表, 会话里记的答案保留."""
        with self._lock:
            self.allowed = allowed
            self.denied = denied

    def permits(self, host: str, port: int) -> bool:
        canonical = _canonical_host(host)
        if canonical is None:
            _log.warning("network_proxy.malformed_host", host=host, port=port)
            return False
        with self._lock:
            denied, allowed = self.denied, self.allowed
        for pattern in denied:
            if matches_domain_pattern(canonical, pattern):
                _log.info("network_proxy.denied_by_rule", host=canonical, port=port)
                return False
        for pattern in allowed:
            if matches_domain_pattern(canonical, pattern):
                return True
        return self._ask(canonical, port)

    def _ask(self, host: str, port: int) -> bool:
        if self.ask is None:
            _log.info("network_proxy.denied_no_rule", host=host, port=port)
            return False
        # 同一 host 的并发请求 (pip 一次开好几条连接) 只问一次: 第一条拿锁去问, 其余等
        # 它问完直接读答案.
        with self._lock:
            if host in self._session_answers:
                return self._session_answers[host]
            gate = self._asking.setdefault(host, threading.Lock())
        with gate:
            with self._lock:
                if host in self._session_answers:
                    return self._session_answers[host]
            try:
                answer = bool(self.ask(host, port))
            except Exception:  # noqa: BLE001 - 问人出错按拒绝算, 不能让代理线程死掉
                _log.exception("network_proxy.ask_failed", host=host)
                answer = False
            with self._lock:
                self._session_answers[host] = answer
            _log.info("network_proxy.user_answered", host=host, allowed=answer)
            return answer


class NetworkProxy:
    """一个线程一个连接的小代理. 生命周期跟工具栈: `start` 一次, `close` 一次."""

    def __init__(self, policy: DomainPolicy) -> None:
        self.policy = policy
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closing = threading.Event()
        self.port: int | None = None
        self.socket_path: str = ""

    # ---- 生命周期 ----

    def start(self, *, unix_socket: str = "") -> None:
        """macOS 监听 127.0.0.1 上的随机端口; Linux 传 `unix_socket` 监听 unix socket,
        由 bubblewrap bind 进围栏."""
        if self._server is not None:
            return
        if unix_socket:
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(unix_socket)
            self.socket_path = unix_socket
        else:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            self.port = int(server.getsockname()[1])
        server.listen(64)
        server.settimeout(0.5)
        self._server = server
        self._thread = threading.Thread(
            target=self._serve, name="forge-network-proxy", daemon=True
        )
        self._thread.start()
        _log.info("network_proxy.started", port=self.port, socket=self.socket_path)

    def close(self) -> None:
        if self._server is None:
            return
        self._closing.set()
        with contextlib.suppress(OSError):
            self._server.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None
        _log.info("network_proxy.closed")

    def _serve(self) -> None:
        assert self._server is not None
        while not self._closing.is_set():
            try:
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    # ---- 一条连接 ----

    def _handle(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(_UPSTREAM_TIMEOUT)
            first = client.recv(1, socket.MSG_PEEK)
            if not first:
                return
            if first[0] == _SOCKS_VERSION:
                upstream = self._socks5(client)
            else:
                upstream = self._http(client)
            if upstream is not None:
                _pipe(client, upstream)
        except (OSError, ValueError) as exc:
            _log.debug("network_proxy.connection_failed", error=str(exc))
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    with contextlib.suppress(OSError):
                        sock.close()

    def _connect(self, host: str, port: int) -> socket.socket | None:
        if not self.policy.permits(host, port):
            return None
        return socket.create_connection((host, port), timeout=_UPSTREAM_TIMEOUT)

    # ---- HTTP 代理: CONNECT 隧道与绝对 URI 转发 ----

    def _http(self, client: socket.socket) -> socket.socket | None:
        head = _read_head(client)
        if head is None:
            _reply(client, 400, "Bad Request")
            return None
        request_line, _, rest = head.partition(b"\r\n")
        parts = request_line.decode("latin-1").split(" ")
        if len(parts) != 3:
            _reply(client, 400, "Bad Request")
            return None
        method, target, version = parts
        if method.upper() == "CONNECT":
            host, port = _split_host_port(target, 443)
            upstream = self._connect(host, port)
            if upstream is None:
                _reply(client, 403, f"Connection to {host}:{port} blocked by sandbox")
                return None
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            return upstream
        # 绝对 URI: `GET http://host/path HTTP/1.1`. 改成 origin-form 转给上游.
        match = re.match(r"^http://([^/]+)(/.*)?$", target, re.IGNORECASE)
        if match is None:
            _reply(client, 400, "Proxy expects an absolute http:// URI or CONNECT")
            return None
        host, port = _split_host_port(match.group(1), 80)
        path = match.group(2) or "/"
        upstream = self._connect(host, port)
        if upstream is None:
            _reply(client, 403, f"Connection to {host}:{port} blocked by sandbox")
            return None
        headers = [
            line
            for line in rest.split(b"\r\n")
            if line and not line.lower().startswith(b"proxy-")
        ]
        rewritten = (
            f"{method} {path} {version}\r\n".encode("latin-1")
            + b"\r\n".join(headers)
            + b"\r\n\r\n"
        )
        upstream.sendall(rewritten)
        return upstream

    # ---- SOCKS5: 无认证, 只支持 CONNECT ----

    def _socks5(self, client: socket.socket) -> socket.socket | None:
        greeting = _read_exact(client, 2)
        methods = _read_exact(client, greeting[1])
        if 0x00 not in methods:
            client.sendall(bytes((_SOCKS_VERSION, 0xFF)))
            return None
        client.sendall(bytes((_SOCKS_VERSION, 0x00)))
        version, command, _, address_type = _read_exact(client, 4)
        if version != _SOCKS_VERSION or command != 0x01:
            client.sendall(_socks_reply(0x07))  # command not supported
            return None
        if address_type == 0x01:
            host = socket.inet_ntoa(_read_exact(client, 4))
        elif address_type == 0x03:
            length = _read_exact(client, 1)[0]
            host = _read_exact(client, length).decode("latin-1")
        elif address_type == 0x04:
            host = socket.inet_ntop(socket.AF_INET6, _read_exact(client, 16))
        else:
            client.sendall(_socks_reply(0x08))  # address type not supported
            return None
        port = int.from_bytes(_read_exact(client, 2), "big")
        try:
            upstream = self._connect(host, port)
        except OSError:
            client.sendall(_socks_reply(0x04))  # host unreachable
            return None
        if upstream is None:
            client.sendall(_socks_reply(0x02))  # connection not allowed by ruleset
            return None
        client.sendall(_socks_reply(0x00))
        return upstream


# ---- 字节层的小工具 ----


def _read_head(client: socket.socket) -> bytes | None:
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = client.recv(4096)
        if not chunk:
            return None
        buffer += chunk
        if len(buffer) > _MAX_HEADER_BYTES:
            return None
    head, _, _ = buffer.partition(b"\r\n\r\n")
    return head


def _read_exact(client: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = client.recv(count - len(data))
        if not chunk:
            raise ValueError("connection closed mid-handshake")
        data += chunk
    return data


def _reply(client: socket.socket, status: int, text: str) -> None:
    body = text.encode("utf-8")
    reason = {400: "Bad Request", 403: "Forbidden"}.get(status, "Error")
    with contextlib.suppress(OSError):
        client.sendall(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode(
                "latin-1"
            )
            + body
        )


def _socks_reply(code: int) -> bytes:
    return bytes((_SOCKS_VERSION, code, 0x00, 0x01, 0, 0, 0, 0, 0, 0))


def _split_host_port(target: str, default_port: int) -> tuple[str, int]:
    """`host:port`, `[v6]:port`, `host` -> (host, port)."""
    if target.startswith("["):
        host, _, remainder = target[1:].partition("]")
        port = remainder[1:] if remainder.startswith(":") else ""
    else:
        host, _, port = target.rpartition(":")
        if not host:
            host, port = port, ""
    if not port:
        return host, default_port
    try:
        return host, int(port)
    except ValueError as exc:
        raise ValueError(f"bad port in {target!r}") from exc


def _pipe(first: socket.socket, second: socket.socket) -> None:
    """双向搬字节直到两边都读完. 一个线程用 select 做, 不为每条连接再开两个线程.

    一边读到 EOF 只关另一边的**写** (半关闭): HTTP 客户端发完请求就 shutdown 写端是
    常见写法, 这时响应还没回来."""
    first.settimeout(None)
    second.settimeout(None)
    pairs = {first: second, second: first}
    readable = set(pairs)
    while readable:
        ready, _, _ = select.select(list(readable), [], [], _IDLE_TIMEOUT)
        if not ready:
            return
        for sock in ready:
            try:
                data = sock.recv(65536)
            except OSError:
                return
            if not data:
                readable.discard(sock)
                with contextlib.suppress(OSError):
                    pairs[sock].shutdown(socket.SHUT_WR)
                continue
            pairs[sock].sendall(data)
