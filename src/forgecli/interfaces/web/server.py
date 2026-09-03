"""裸 ``forge`` 的本地 Web 服务启动器。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import signal
import socket
import threading
import urllib.parse
import webbrowser
from collections.abc import Generator
from pathlib import Path
from types import FrameType

import uvicorn
from rich.console import Console

from forgecli.infrastructure.config import config_dir
from forgecli.infrastructure.project import ProjectLockedError
from forgecli.interfaces.exit_codes import ExitCode
from forgecli.interfaces.runtime.logging_wiring import start_observability
from forgecli.interfaces.runtime.project_runtime import (
    ProjectRuntimeRegistry,
    build_project_service,
)
from forgecli.interfaces.web.app import create_app
from forgecli.shared import __version__
from forgecli.shared.observability.log import get_log

_log = get_log(__name__)

DEFAULT_PORT = 8765

_GRACEFUL_SHUTDOWN_SECONDS = 5
_SECRETS_FILE = "web-secrets.json"


class _ForgeServer(uvicorn.Server):
    """由 Forge 统一接管信号，避免继承自 ``make`` 的忽略状态泄漏进服务。"""

    def __init__(self, config: uvicorn.Config, *, stopping: asyncio.Event) -> None:
        super().__init__(config)
        self._stopping = stopping

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        """先通知 SSE 长连接收尾，再走 Uvicorn 的等待流程。

        Uvicorn 的 lifespan shutdown 排在"等所有连接结束"之后，所以长连接不能等到那时
        才知道服务要停：必须在这里置位，否则 Ctrl-C 会一直卡在等待连接关闭上。
        """
        self._stopping.set()
        await super().shutdown(sockets)


@contextlib.contextmanager
def _shutdown_signals(server: uvicorn.Server, console: Console) -> Generator[None]:
    """显式把 Ctrl-C/SIGTERM 转成 Uvicorn 的协作式退出。"""

    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def request_shutdown(sig: int, _frame: FrameType | None) -> None:
        if server.should_exit and sig == signal.SIGINT:
            server.force_exit = True
        elif not server.should_exit:
            console.print("[dim]正在停止 Forge Web…[/]")
        server.should_exit = True

    handled = (signal.SIGINT, signal.SIGTERM)
    previous = {sig: signal.signal(sig, request_shutdown) for sig in handled}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def load_web_secrets() -> tuple[str, str]:
    """读取或创建跨重启复用的 ``(session, csrf)`` 密钥。

    端口固定加上密钥固定，重启 Forge 之后已经打开的浏览器窗口才能自己连回来：
    否则每次重启都换 cookie，页面只能重新走一遍启动链接。文件按 0600 存放在用户配置
    目录，与服务只监听 loopback 是同一层信任假设。
    """
    path = config_dir() / _SECRETS_FILE
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        session, csrf = str(stored["session"]), str(stored["csrf"])
        if len(session) >= 32 and len(csrf) >= 16:
            return session, csrf
    except (OSError, ValueError, KeyError, TypeError):
        pass
    session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先以 0600 创建再写入：先写后 chmod 会留下一个短暂的可读窗口。
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"session": session, "csrf": csrf}, handle)
    return session, csrf


def run(*, port: int = DEFAULT_PORT, open_browser: bool = False) -> int:
    """只监听 loopback，并用一次性启动令牌打开控制面。"""
    console = Console()
    status = start_observability()
    _log.info(
        "forge.start",
        entry="web",
        version=__version__,
        cwd=str(Path.cwd()),
        port=port,
        open_browser=open_browser,
        log_file=None if status.file is None else str(status.file),
        log_level=status.level,
    )
    project_service = build_project_service()
    registry = ProjectRuntimeRegistry(project_service)
    current = project_service.find_trusted(Path.cwd())
    if current is not None:
        try:
            registry.activate(current.project_id)
        except ProjectLockedError as exc:
            _log.warning("forge.refused", reason="project_locked", message=exc.message)
            console.print(f"[yellow]{exc.message}[/]")
            return ExitCode.PROJECT_LOCKED

    session_token, csrf_token = load_web_secrets()
    app = create_app(
        registry=registry, session_token=session_token, csrf_token=csrf_token
    )
    token: str = app.state.security.boot_token
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            _log.error("web.bind_failed", port=port, message=str(exc))
            console.print(
                f"[red]端口 {port} 无法监听[/]：{exc.strerror}。\n"
                "多半是另一个 Forge 还在运行；可以先停掉它，或用 "
                "[bold]forge --port <其他端口>[/] 换一个端口。"
            )
            return ExitCode.PORT_BUSY
        sock.listen(2048)
        selected_port = int(sock.getsockname()[1])
        _log.info("web.listening", port=selected_port)
        url = (
            f"http://127.0.0.1:{selected_port}/boot?token="
            f"{urllib.parse.quote(token, safe='')}"
        )
        # 启动链接一直打印：默认不抢占浏览器，用户自己决定什么时候打开控制面。
        console.print(
            f"Forge Web 已启动：[bold cyan]http://127.0.0.1:{selected_port}[/]\n"
            f"启动链接：{url}\n"
            "按 Ctrl-C 停止服务。"
        )
        if open_browser:
            opener = threading.Timer(0.25, webbrowser.open, args=(url,))
            opener.daemon = True
            opener.start()
        config = uvicorn.Config(
            app,
            log_level="warning",
            access_log=False,
            # 兜底：即使有连接不肯收尾，也不能把 Ctrl-C 变成无限等待。
            timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_SECONDS,
        )
        server = _ForgeServer(config, stopping=app.state.stopping)
        with (
            _shutdown_signals(server, console),
            contextlib.suppress(KeyboardInterrupt),
        ):
            server.run(sockets=[sock])
        _log.info("forge.stop", entry="web", port=selected_port)
        return ExitCode.OK
    finally:
        sock.close()
        registry.close()
