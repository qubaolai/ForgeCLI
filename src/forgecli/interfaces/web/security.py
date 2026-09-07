"""本地控制面的准入: Host 白名单, 会话 cookie, CSRF 与安全响应头。

从 ``app.py`` 分出来 (ADR-0048 决策 5): 它与任何一个资源都无关, 而混在路由表里的
后果是每次加一条路由都要从这段中间件读起。
"""

from __future__ import annotations

import secrets

from fastapi import Request, Response, status
from fastapi.responses import HTMLResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from forgecli.shared.observability.log import get_log

__all__ = [
    "CSRF_COOKIE",
    "SESSION_COOKIE",
    "LocalControlPlaneGuard",
    "SecurityState",
]

_log = get_log(__name__)

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
SESSION_COOKIE = "forge_web_session"
CSRF_COOKIE = "forge_web_csrf"
_OPEN_PATHS = {"/boot", "/api/v1/health"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    ),
}
# 构建产物带内容哈希, 可以长期缓存; 入口 HTML 必须每次回源, 否则升级 Forge 之后浏览器
# 还在跑上一版前端 —— 那是一类很难自查的故障: 代码改了, 用户看到的行为没改。
_IMMUTABLE_PREFIX = "/assets/"
_ASSET_CACHE = "public, max-age=31536000, immutable"
_DOCUMENT_CACHE = "no-cache"
# 直接用浏览器打开陈旧地址时给出可操作的说明，而不是一行裸 "Unauthorized"。
_UNAUTHORIZED_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forge · 本地会话已失效</title>
<style>
:root { color-scheme: light dark; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center;
  padding: 32px; background: #0d0f13; color: #e7e9ee;
  font-family: Inter, ui-sans-serif, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 460px; }
h1 { margin: 0 0 12px; font-size: 20px; }
p { margin: 0 0 10px; color: #9198a7; line-height: 1.75; font-size: 14px; }
code { padding: 2px 6px; border-radius: 5px; background: #e8ebef; color: #111827;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .92em; }
@media (prefers-color-scheme: light) {
  body { background: #f4f5f7; color: #1c2430; }
  p { color: #687182; }
}
</style></head>
<body><main>
<h1>本地会话已失效</h1>
<p>Forge 每次启动都会换用新的一次性启动令牌，之前的地址不再有效。</p>
<p>请回到运行 <code>forge</code> 的终端，复制那里打印的启动链接重新打开控制面。</p>
</main></body></html>
"""


class SecurityState:
    """一次进程生命周期内的三个密钥。"""

    def __init__(
        self,
        boot_token: str | None = None,
        *,
        session: str | None = None,
        csrf: str | None = None,
    ) -> None:
        self.boot_token = boot_token or secrets.token_urlsafe(32)
        # 会话与 CSRF 密钥可以由启动器跨重启复用：否则每次重启都会把已经打开的
        # 浏览器窗口踢下线，自动重连也就没有意义了。
        self.session = session or secrets.token_urlsafe(32)
        self.csrf = csrf or secrets.token_urlsafe(24)


class LocalControlPlaneGuard:
    """本地控制面的 Host/会话/CSRF 校验与安全响应头。

    必须是纯 ASGI 中间件：``BaseHTTPMiddleware`` 会把响应体搬进内存流再转发，SSE 长连接
    因此既看不到客户端断连，也会在服务退出时留下一串 ``CancelledError`` 栈。
    """

    def __init__(self, app: ASGIApp, security: SecurityState) -> None:
        self.app = app
        self.security = security

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        denial = self._denial(Request(scope))
        if denial is not None:
            _log.warning(
                "web.denied",
                method=scope.get("method"),
                path=scope.get("path"),
                status=denial.status_code,
            )
            denial.headers.update(_SECURITY_HEADERS)
            await denial(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        method = str(scope.get("method", ""))
        raw_query = scope.get("query_string", b"")
        query = (
            raw_query.decode("utf-8", "replace") if isinstance(raw_query, bytes) else ""
        )

        async def send_hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                _log.info(
                    "web.request",
                    method=method,
                    path=path,
                    query=query,
                    status=message.get("status"),
                )
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers[name] = value
                if "cache-control" not in headers:
                    headers["Cache-Control"] = (
                        _ASSET_CACHE
                        if path.startswith(_IMMUTABLE_PREFIX)
                        else _DOCUMENT_CACHE
                    )
            await send(message)

        await self.app(scope, receive, send_hardened)

    def _denial(self, request: Request) -> Response | None:
        if request.url.hostname not in _ALLOWED_HOSTS:
            return Response("Invalid Host", status_code=status.HTTP_400_BAD_REQUEST)
        if (
            request.url.path not in _OPEN_PATHS
            and request.cookies.get(SESSION_COOKIE) != self.security.session
        ):
            if "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(
                    _UNAUTHORIZED_PAGE, status_code=status.HTTP_401_UNAUTHORIZED
                )
            return Response("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)
        if request.method not in _WRITE_METHODS:
            return None
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin != expected:
            return Response("Invalid Origin", status_code=status.HTTP_403_FORBIDDEN)
        if request.url.path != "/boot" and (
            request.cookies.get(CSRF_COOKIE) != self.security.csrf
            or request.headers.get("x-csrf-token") != self.security.csrf
        ):
            return Response("Invalid CSRF token", status_code=status.HTTP_403_FORBIDDEN)
        return None
