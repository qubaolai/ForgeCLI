"""子进程输出的内存上界.

早先是 `communicate()` 读完再截断: 上限只影响交给上层的字符串大小, 拦不住内存耗尽.
一条 `yes` 能在超时之前把进程撑爆, 而超时本身也救不了 —— 内存先耗尽.
"""

from __future__ import annotations

import sys
import time

from forgecli.application.tools.command_executor import CommandRequest
from forgecli.infrastructure.execution.local_command_executor import (
    LocalCommandExecutor,
)


def _request(script: str, **overrides: object) -> CommandRequest:
    base: dict[str, object] = {
        "argv": (sys.executable, "-c", script),
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 20.0,
        "max_output_bytes": 4096,
    }
    base.update(overrides)
    return CommandRequest(**base)  # type: ignore[arg-type]


def test_unbounded_output_is_capped_without_reading_it_all() -> None:
    """无限输出必须在上限处停止累积, 而且进程要能正常结束.

    读满就不读了会让子进程卡在写管道上, 于是它永远不退出 —— 所以读线程必须继续把管道
    读空, 只是不再累积.
    """
    executor = LocalCommandExecutor()
    # 写 64 MB, 远超 4 KB 上限.
    script = "import sys\nfor _ in range(1024):\n    sys.stdout.write('x' * 65536)\n"
    started = time.monotonic()

    outcome = executor.run(_request(script))

    assert outcome.truncated is True
    assert len(outcome.stdout.encode("utf-8")) <= 4096
    assert outcome.timed_out is False, "进程应当自己跑完, 而不是被超时杀掉"
    assert outcome.exit_code == 0
    assert time.monotonic() - started < 20.0


def test_stderr_is_capped_independently() -> None:
    executor = LocalCommandExecutor()
    script = "import sys\nfor _ in range(256):\n    sys.stderr.write('e' * 65536)\n"

    outcome = executor.run(_request(script))

    assert outcome.truncated is True
    assert len(outcome.stderr.encode("utf-8")) <= 4096


def test_output_under_the_limit_is_not_marked_truncated() -> None:
    executor = LocalCommandExecutor()
    outcome = executor.run(_request("print('hello')"))

    assert outcome.stdout.strip() == "hello"
    assert outcome.truncated is False
    assert outcome.exit_code == 0


def test_stdin_is_delivered_and_closed() -> None:
    """关掉 stdin 是必须的: 等着读输入的子进程不会自己退出."""
    executor = LocalCommandExecutor()
    script = "import sys\nsys.stdout.write(sys.stdin.read().upper())\n"

    outcome = executor.run(_request(script, stdin="abc"))

    assert outcome.stdout == "ABC"
    assert outcome.timed_out is False


def test_a_hanging_process_still_times_out() -> None:
    executor = LocalCommandExecutor()
    outcome = executor.run(
        _request("import time\ntime.sleep(30)\n", timeout_seconds=1.0)
    )

    assert outcome.timed_out is True


def test_a_process_writing_forever_still_times_out() -> None:
    """既无限输出又永不退出: 两个机制都要成立."""
    executor = LocalCommandExecutor()
    script = (
        "import sys\nwhile True:\n    sys.stdout.write('y' * 4096)\n"
        "    sys.stdout.flush()\n"
    )

    outcome = executor.run(_request(script, timeout_seconds=1.5))

    assert outcome.timed_out is True
    assert len(outcome.stdout.encode("utf-8")) <= 4096
