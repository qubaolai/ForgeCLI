"""最小的 JSON-RPC-over-stdio 客户端: initialize 握手, didOpen / didChange 全量同步,
请求等回应 (读线程切消息进队列), 服务器主动发来的请求给最保守的回应, 通知忽略."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = ["LspClient", "LspError"]

_REQUEST_TIMEOUT_SECONDS = 60.0


class LspError(Exception):
    """服务器起不来, 回错误, 或没在时限内回应."""


class LspClient:
    def __init__(
        self, argv: tuple[str, ...], root: str, environment: Mapping[str, str]
    ) -> None:
        try:
            self._process = subprocess.Popen(  # noqa: S603 - argv 来自服务器表, 不含用户输入
                list(argv),
                cwd=root,
                env=dict(environment),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise LspError(f"language server failed to start: {exc}") from exc
        self._root = root
        self._next_id = 1
        self._responses: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._documents: dict[str, int] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._initialize()

    # ---- 协议 ----

    def _initialize(self) -> None:
        self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": Path(self._root).as_uri(),
                "rootPath": self._root,
                "capabilities": {
                    "textDocument": {
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    },
                    "workspace": {"workspaceFolders": True},
                },
                "workspaceFolders": [
                    {"uri": Path(self._root).as_uri(), "name": Path(self._root).name}
                ],
            },
        )
        self._notify("initialized", {})

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            slot: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._responses[request_id] = slot
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        try:
            message = slot.get(timeout=_REQUEST_TIMEOUT_SECONDS)
        except queue.Empty as exc:
            raise LspError(f"{method} timed out") from exc
        finally:
            with self._lock:
                self._responses.pop(request_id, None)
        error = message.get("error")
        if error is not None:
            raise LspError(f"{method}: {error.get('message', error)}")
        return message.get("result")

    def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def open_document(self, path: str, language_id: str, text: str) -> None:
        uri = Path(path).as_uri()
        with self._lock:
            version = self._documents.get(uri)
        if version is None:
            with self._lock:
                self._documents[uri] = 1
            self._notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": 1,
                        "text": text,
                    }
                },
            )
            return
        with self._lock:
            self._documents[uri] = version + 1
        self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version + 1},
                "contentChanges": [{"text": text}],
            },
        )

    def close(self) -> None:
        try:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id,
                    "method": "shutdown",
                    "params": None,
                }
            )
            self._notify("exit", {})
        except LspError:
            pass
        try:
            self._process.terminate()
            self._process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self._process.kill()

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    # ---- 传输 ----

    def _send(self, message: Mapping[str, Any]) -> None:
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        stdin = self._process.stdin
        if stdin is None:
            raise LspError("language server stdin closed")
        with self._write_lock:
            try:
                stdin.write(header + body)
                stdin.flush()
            except (OSError, ValueError) as exc:
                raise LspError(f"language server write failed: {exc}") from exc

    def _read_loop(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            return
        while True:
            length = 0
            while True:
                line = stdout.readline()
                if not line:
                    return
                if line in (b"\r\n", b"\n"):
                    break
                if line.lower().startswith(b"content-length:"):
                    try:
                        length = int(line.split(b":", 1)[1].strip())
                    except ValueError:
                        length = 0
            if length <= 0:
                continue
            body = stdout.read(length)
            if not body:
                return
            try:
                message = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            self._dispatch(message)

    def _dispatch(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        if "id" in message and "method" not in message:
            with self._lock:
                slot = self._responses.get(int(message["id"]))
            if slot is not None:
                slot.put(message)
            return
        if "method" in message and "id" in message:
            # 服务器主动发来的请求: 给最保守的回应.
            method = message["method"]
            result: Any = None
            if method == "workspace/configuration":
                params = message.get("params") or {}
                items = params.get("items") or []
                result = [None for _ in items]
            elif method == "client/registerCapability":
                result = None
            try:
                self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
            except LspError:
                return
        # 通知忽略.
