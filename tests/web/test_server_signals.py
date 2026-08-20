from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX 进程组信号测试")
def test_make_run_stops_after_one_sigint(tmp_path: Path) -> None:
    """回归：make 等待的 Forge 子进程必须显式接管继承来的 SIGINT。"""

    repository = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["FORGE_CONFIG_DIR"] = str(tmp_path / "forge-home")
    process = subprocess.Popen(
        ["make", "run"],
        cwd=repository,
        env={**environment, "FORGE_RUN_ARGS": "--port 0"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output: list[str] = []
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 8.0
            started = False
            while time.monotonic() < deadline and process.poll() is None:
                if not selector.select(timeout=0.1):
                    continue
                line = process.stdout.readline()
                output.append(line)
                if "Forge Web 已启动" in line:
                    started = True
                    break
        assert started, "服务未按时启动：" + "".join(output)

        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=5.0)
        group_deadline = time.monotonic() + 5.0
        while time.monotonic() < group_deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("Ctrl-C 后 Forge 子进程仍留在 make 的进程组中")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5.0)
        else:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                os.killpg(process.pid, signal.SIGKILL)
        if process.stdout is not None:
            process.stdout.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX 进程组信号测试")
def test_sigint_stops_server_while_event_stream_is_open(tmp_path: Path) -> None:
    """回归：浏览器挂着 SSE 时，一次 Ctrl-C 也要干净退出，且不打印异常栈。"""

    repository = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["FORGE_CONFIG_DIR"] = str(tmp_path / "forge-home")
    # Rich 面向管道时按 80 列折行，会把启动链接拆断。
    environment["COLUMNS"] = "400"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from forgecli.interfaces.cli.app import main; main()",
            "--port",
            "0",
        ],
        cwd=repository,
        env={**environment, "PYTHONPATH": str(repository / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output: list[str] = []
    reader = threading.Thread(target=_drain, args=(process, output), daemon=True)
    reader.start()
    try:
        boot_url = _await_boot_url(process, output)
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        opener.open(boot_url, timeout=5.0).read()
        base = boot_url.split("/boot", 1)[0]
        csrf = json.loads(opener.open(f"{base}/api/v1/bootstrap", timeout=5.0).read())
        project = _post(
            opener, f"{base}/api/v1/projects", csrf, {"path": str(repository)}
        )
        _post(
            opener,
            f"{base}/api/v1/projects/{project['project_id']}/activate",
            csrf,
            None,
        )
        stream = opener.open(f"{base}/api/v1/events", timeout=5.0)
        threading.Thread(target=_consume, args=(stream,), daemon=True).start()
        time.sleep(0.5)

        started = time.monotonic()
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=10.0)
        assert process.returncode == 0, "".join(output)
        assert time.monotonic() - started < 8.0, "Ctrl-C 后服务没有及时退出"
        assert "Traceback" not in "".join(output), "".join(output)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5.0)
        if process.stdout is not None:
            process.stdout.close()


def _drain(process: subprocess.Popen[str], sink: list[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        sink.append(line)


def _await_boot_url(process: subprocess.Popen[str], output: list[str]) -> str:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        found = re.search(r"启动链接：(\S+)", "".join(output))
        if found:
            return found.group(1)
        if process.poll() is not None:
            break
        time.sleep(0.05)
    raise AssertionError("服务未按时启动：" + "".join(output))


def _post(
    opener: urllib.request.OpenerDirector,
    url: str,
    bootstrap: dict[str, str],
    body: dict[str, str] | None,
) -> dict[str, str]:
    payload = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=payload or b"", method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("X-CSRF-Token", bootstrap["csrf_token"])
    return json.loads(opener.open(request, timeout=5.0).read())


def _consume(stream: object) -> None:
    with contextlib.suppress(Exception):
        for _ in stream:  # type: ignore[attr-defined]
            continue
