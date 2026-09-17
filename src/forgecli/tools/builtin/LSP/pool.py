"""每种语言一个客户端, 项目关闭时关掉. 同一客户端上的查询加锁串行 (并发批里不交错)."""

from __future__ import annotations

import threading
from collections.abc import Mapping

from forgecli.tools.builtin.LSP.client import LspClient, LspError
from forgecli.tools.builtin.LSP.servers import LanguageServer

__all__ = ["LspServerPool"]


class LspServerPool:
    def __init__(self, root: str, environment: Mapping[str, str]) -> None:
        self._root = root
        self._environment = environment
        self._clients: dict[str, LspClient] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def client_for(self, server: LanguageServer, executable: str) -> LspClient:
        with self._lock:
            client = self._clients.get(server.name)
            if client is not None and client.alive:
                return client
            argv = (executable, *server.argv[1:])
            client = LspClient(argv, self._root, self._environment)
            self._clients[server.name] = client
            self._locks.setdefault(server.name, threading.Lock())
            return client

    def lock_for(self, server: LanguageServer) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(server.name, threading.Lock())

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except LspError:
                continue
